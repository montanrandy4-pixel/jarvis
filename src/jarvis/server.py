"""The JARVIS app: a local web server and the single-page UI it serves.

Built on the standard library's HTTP server. The app is a personal assistant on
one machine, not a multi-tenant service, so it binds to the loopback interface
and keeps one conversation.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .agent import Agent
from .backends import BackendError
from .launcher import LOOPBACK, local_url, open_window, running_instance
from .memory import MemoryStore
from .persona import CALL_NOTE
from .tools import build_registry, system
from .tools.timers import SERVICE as TIMERS

log = logging.getLogger("jarvis.server")

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")

WEB_ROOT = Path(__file__).parent / "web"
# How long a tool call waits for the user to allow or deny it.
CONFIRM_TIMEOUT = 120.0
# How long a turn may go without producing anything before the app gives up.
TURN_TIMEOUT = 300.0
# How long the browser's idle poll waits for something to announce.
POLL_TIMEOUT = 25.0


class Session:
    """One conversation, plus the state the UI needs to render it."""

    def __init__(self, config, *, backend=None):
        self.config = config
        self.memory = MemoryStore(config.memory_path)
        self.registry = build_registry(config, self.memory, confirm=self._confirm)
        self.agent = Agent(
            config,
            self.registry,
            self.memory,
            voice=True,
            backend=backend,
            on_tool=self._tool_ran,
        )
        self.lock = threading.Lock()
        # Each turn gets its own queue, so an event produced by one turn can
        # never be delivered into the next one's stream.
        self._turn_events: queue.Queue | None = None
        # Anything that happens between turns (a timer going off) waits here
        # for the browser's long poll.
        self.announcements: queue.Queue = queue.Queue()
        self._pending: dict[str, dict] = {}
        self._cancel: threading.Event | None = None
        TIMERS.on_fire = self._timer_fired

    # -- event plumbing --------------------------------------------------

    def emit(self, event: dict) -> None:
        """Send an event to the turn in progress, or hold it for the browser."""
        stream = self._turn_events
        (stream or self.announcements).put(event)

    def _tool_ran(self, label: str, result) -> None:
        self.emit({"type": "tool", "label": label, "error": result.is_error})

    def _timer_fired(self, timer) -> None:
        label = f" for {timer.label}" if timer.label else ""
        self.emit({"type": "announce", "text": f"Your timer{label} is up."})

    # -- confirmations ---------------------------------------------------

    def _confirm(self, name: str, summary: str) -> bool:
        """Ask the browser, and block this tool until the user answers."""
        request_id = uuid.uuid4().hex[:8]
        answered = threading.Event()
        self._pending[request_id] = {"event": answered, "allowed": False}
        self.emit(
            {"type": "confirm", "id": request_id, "tool": name, "summary": summary}
        )
        if not answered.wait(CONFIRM_TIMEOUT):
            log.info("confirmation %s timed out", request_id)
        decision = self._pending.pop(request_id, {})
        return bool(decision.get("allowed"))

    def resolve(self, request_id: str, allowed: bool) -> bool:
        pending = self._pending.get(request_id)
        if not pending:
            return False
        pending["allowed"] = allowed
        pending["event"].set()
        return True

    # -- turns -----------------------------------------------------------

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()

    def start_turn(self, text: str, *, call: bool = False) -> queue.Queue:
        """Begin an exchange and return the queue its events arrive on."""
        stream: queue.Queue = queue.Queue()
        self._turn_events = stream
        thread = threading.Thread(
            target=self._run_turn, args=(text, call), daemon=True
        )
        thread.start()
        return stream

    def _run_turn(self, text: str, call: bool = False) -> None:
        """Run one exchange, emitting events as it goes."""
        self._cancel = threading.Event()
        try:
            turn = self.agent.reply(
                text,
                on_text=lambda chunk: self.emit({"type": "text", "value": chunk}),
                cancel=self._cancel,
                note=CALL_NOTE if call else "",
            )
            self.emit(
                {
                    "type": "done",
                    "text": turn.text,
                    "error": turn.error,
                    "cancelled": turn.stop_reason == "cancelled",
                    "tools": turn.tool_calls,
                }
            )
        except BackendError as exc:
            self.emit({"type": "done", "text": "", "error": str(exc)})
        except Exception as exc:  # The app must survive a bug in a tool.
            log.exception("turn failed")
            self.emit({"type": "done", "text": "", "error": f"Internal error: {exc}"})
        finally:
            self._cancel = None
            # Nothing more belongs to this turn; later events are announcements.
            self._turn_events = None

    def state(self) -> dict:
        ready, detail = self.agent.backend.health()
        return {
            "backend": self.agent.backend.name,
            "model": self.agent.backend.model,
            "ready": ready,
            "detail": detail,
            "tools": sorted(self.registry.tools),
            "memories": [
                {"id": m.id, "text": m.text} for m in self.memory.all()
            ],
            "name": self.config.name,
            "address_as": self.config.address_user_as,
            "user_name": self.config.user_name,
            "wake_word": self.config.wake_word,
            "shell": self.config.shell,
        }

    def telemetry(self) -> dict:
        """What the HUD shows around the reactor: vital signs and timers."""
        data = system.snapshot()
        data["timers"] = [
            {
                "id": t.id,
                "label": t.label,
                "remaining_s": round(t.remaining(), 1),
                "duration_s": t.duration,
            }
            for t in TIMERS.all()
        ]
        return data


class Handler(BaseHTTPRequestHandler):
    server_version = "JARVIS"
    session: Session = None  # Set on the server instance below.

    def log_message(self, fmt, *args):  # Quieter than the default.
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _trusted(self) -> bool:
        """Refuse requests that a web page elsewhere tricked the browser into.

        Any site you visit can make your browser send requests to 127.0.0.1.
        Two checks stop it steering JARVIS: the Origin of a cross-site request
        will not match the Host it was sent to, and a DNS-rebinding page, which
        does share the origin, arrives under its own hostname rather than a
        loopback one.
        """
        host = (self.headers.get("Host") or "").strip().lower()
        bound = self.server.server_address[0]
        if bound in LOOPBACK or bound.startswith("127."):
            name = urlsplit(f"//{host}").hostname or ""
            if name not in LOOPBACK and not name.startswith("127."):
                return False
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            if urlsplit(origin).netloc.lower() != host:
                return False
        return True

    def _refuse(self) -> None:
        log.warning("refused %s %s from origin %s",
                    self.command, self.path, self.headers.get("Origin"))
        self._send_json({"error": "forbidden"}, 403)

    # -- helpers ---------------------------------------------------------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        if not self._trusted():
            return self._refuse()
        route = self.path.split("?")[0]
        if route == "/":
            self._send_file(WEB_ROOT / "index.html")
        elif route == "/manifest.webmanifest":
            self._send_file(WEB_ROOT / "manifest.webmanifest")
        elif route == "/api/ping":
            self._send_json({"app": "jarvis", "version": __version__})
        elif route == "/api/state":
            self._send_json(self.session.state())
        elif route == "/api/telemetry":
            self._send_json(self.session.telemetry())
        elif route == "/api/events":
            self._poll_announcements()
        elif route.startswith("/static/"):
            name = route[len("/static/"):]
            # Keep the served set to the app's own files.
            if "/" in name or ".." in name:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_file(WEB_ROOT / name)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._trusted():
            return self._refuse()
        route = self.path.split("?")[0]
        if route == "/api/chat":
            self._chat()
        elif route == "/api/confirm":
            body = self._body()
            ok = self.session.resolve(body.get("id", ""), bool(body.get("allow")))
            self._send_json({"ok": ok})
        elif route == "/api/cancel":
            self.session.cancel()
            self._send_json({"ok": True})
        elif route == "/api/reset":
            self.session.agent.reset()
            self._send_json({"ok": True})
        elif route == "/api/forget":
            removed = self.session.memory.forget(self._body().get("id", ""))
            self._send_json({"ok": removed})
        elif route == "/api/shutdown":
            self._send_json({"ok": True})
            # shutdown() waits for serve_forever to return, and this request is
            # being handled inside it, so it has to run on another thread.
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send_json({"error": "not found"}, 404)

    def _poll_announcements(self) -> None:
        """Hold the request open until something happens, or time out empty."""
        events = []
        try:
            events.append(self.session.announcements.get(timeout=POLL_TIMEOUT))
            while True:  # Take anything else already waiting.
                events.append(self.session.announcements.get_nowait())
        except queue.Empty:
            pass
        self._send_json({"events": events})

    def _chat(self) -> None:
        """Run a turn, streaming events to the browser as server-sent events."""
        body = self._body()
        text = (body.get("text") or "").strip()
        if not text:
            self._send_json({"error": "nothing to say"}, 400)
            return
        if not self.session.lock.acquire(blocking=False):
            self._send_json({"error": "already thinking"}, 409)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        # No keep-alive: BaseHTTPRequestHandler would hold the connection open
        # after the last event, and the browser would wait for an end that
        # never comes. The stream ends when the connection closes.
        self.end_headers()

        stream = self.session.start_turn(text, call=bool(body.get("call")))
        try:
            while True:
                try:
                    event = stream.get(timeout=TURN_TIMEOUT)
                except queue.Empty:
                    self._send_event(
                        {"type": "done", "text": "", "error": "That took too long."}
                    )
                    self.session.cancel()
                    return
                self._send_event(event)
                if event["type"] == "done":
                    return
        except (BrokenPipeError, ConnectionResetError):
            # The tab was closed or refreshed mid-reply; stop generating.
            self.session.cancel()
        finally:
            self.session.lock.release()

    def _send_event(self, event: dict) -> None:
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.flush()


def build_server(config, *, backend=None) -> tuple[ThreadingHTTPServer, Session]:
    """Create the HTTP server and its session without starting to serve."""
    session = Session(config, backend=backend)
    handler = type("BoundHandler", (Handler,), {"session": session})
    server = ThreadingHTTPServer((config.host, config.port), handler)
    server.daemon_threads = True
    return server, session


def serve(config, *, open_browser: bool | None = None, fragment: str = "") -> int:
    """Start the app, or bring up the copy already running. Blocks until stopped.

    ``fragment`` is added to the address the window opens, e.g. ``#call`` to
    start a voice call as soon as it loads.
    """
    url = local_url(config.host, config.port)
    should_open = config.open_browser if open_browser is None else open_browser

    if running_instance(url):
        # A second launch (the icon, the hotkey) is a request to see JARVIS.
        print(f"JARVIS is already running at {url}")
        if should_open:
            open_window(url + fragment, app_window=config.app_window)
        return 0

    try:
        server, session = build_server(config)
    except OSError as exc:
        print(
            f"Cannot start on port {config.port}: {exc.strerror or exc}.\n"
            f"Another program is using it. Try: jarvis --port {config.port + 1}",
            file=sys.stderr,
        )
        return 1

    ready, detail = session.agent.backend.health()
    print(f"JARVIS is running at {url}")
    print(f"  model   {session.agent.backend.description}")
    print(f"  status  {'ready' if ready else 'NOT READY -- ' + detail}")
    if not ready:
        print("\nThe app will still open; it will work once the model is reachable.")
    print("\nPress Ctrl-C to stop, or use Shut down in the app's Settings.")

    if should_open:
        threading.Timer(
            0.5, lambda: open_window(url + fragment, app_window=config.app_window)
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
        TIMERS.cancel_all()
        path = session.agent.save_transcript()
        if path:
            log.info("transcript saved to %s", path)
    return 0
