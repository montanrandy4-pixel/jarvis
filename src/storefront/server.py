"""A local web server showing what the agent is doing.

The watch loop already knows everything worth knowing; this puts it somewhere
you can look at it. It serves a single page and two endpoints:

* ``/api/state``  -- a snapshot, for a page that has just loaded
* ``/api/events`` -- server-sent events, so the page keeps up without polling

Everything stays on the machine. The page is served to localhost, the state
never leaves the process, and no credential is ever sent to the browser --
the token lives in the agent, and the browser only ever sees findings.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import threading
import urllib.parse
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import agent, alerts as alerts_mod, audit as audit_mod, voice as voice_mod
from . import watch as watch_mod
from .alerts import CRITICAL, INFO, WARNING
from .config import Config

log = logging.getLogger("storefront.server")

WEB_ROOT = Path(__file__).parent / "web"
# More than this many browser tabs is somebody's mistake, not a use case.
MAX_LISTENERS = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Workspace:
    """Everything the page needs, and the fan-out that keeps it current."""

    config: Config
    dispatcher: alerts_mod.Dispatcher
    state: watch_mod.WatchState = field(default_factory=watch_mod.WatchState)

    shop_name: str = ""
    products: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    last_pass_at: str = ""
    next_pass_at: float = 0.0
    scanning: str = ""
    errors: list[str] = field(default_factory=list)

    _listeners: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- fan-out -----------------------------------------------------------
    def listen(self) -> queue.Queue | None:
        with self._lock:
            if len(self._listeners) >= MAX_LISTENERS:
                return None
            q: queue.Queue = queue.Queue(maxsize=64)
            self._listeners.append(q)
            return q

    def drop(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def emit(self, kind: str, **data) -> None:
        event = {"kind": kind, "at": _now(), **data}
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(event)
            except queue.Full:
                # A tab that cannot keep up is dropped rather than allowed to
                # back-pressure the watch loop.
                self.drop(q)

    # -- snapshot ----------------------------------------------------------
    def snapshot(self) -> dict:
        remaining = max(0, int(self.next_pass_at - time.time())) if self.next_pass_at else 0
        return {
            "shop": {"name": self.shop_name, "domain": self.config.store_domain},
            "pass": {
                "n": self.state.passes,
                "at": self.last_pass_at,
                "next_in": remaining,
                "interval": self.config.interval_minutes * 60,
                "scanning": self.scanning,
                "failures": self.state.consecutive_failures,
            },
            "products": self.products,
            "alerts": self.alerts[-40:],
            "summary": self.summary,
            "checks": self.checks,
            "errors": self.errors,
            "dry_run": self.config.dry_run,
        }

    # -- the pass ----------------------------------------------------------
    def run_pass(self, *, since_days: int = 7, browser_checks: bool = True) -> None:
        self.scanning = "catalogue"
        self.emit("scan", stage="catalogue")
        found = []
        try:
            result = agent.run_once(
                self.config, since_days=since_days, tag=not self.config.dry_run
            )
            self.state.passes += 1
            self.state.consecutive_failures = 0
            self.errors = list(result.errors)

            self._absorb_catalog(result)
            self.scanning = "orders"
            self.emit("scan", stage="orders")
            self._absorb_orders(result)

            if result.audit_report:
                found += watch_mod.catalogue_alerts(result.audit_report)
            found += watch_mod.order_alerts(result.order_views)
            found += watch_mod.sales_alerts(result.summary)

            due = (
                time.monotonic() - self.state.last_storefront_check
                >= self.config.storefront_check_minutes * 60
            )
            if browser_checks and due and result.live_handles:
                self.state.last_storefront_check = time.monotonic()
                self.scanning = "storefront"
                self.emit("scan", stage="storefront")
                from . import synthetic

                report = synthetic.check_storefront(
                    self.config.store_domain,
                    product_handles=result.live_handles,
                    headless=True,
                )
                self.checks["storefront"] = {
                    "at": _now(),
                    "ok": report.ok,
                    "checks": [
                        {"name": c.name, "ok": c.ok, "detail": c.detail}
                        for c in report.checks
                    ],
                }
                found += watch_mod.storefront_alerts(report)
        except Exception as exc:  # noqa: BLE001 -- a failed pass is a finding
            self.state.consecutive_failures += 1
            self.errors = [str(exc)[:200]]
            log.exception("pass failed")
            found.append(alerts_mod.Alert(
                CRITICAL, "the watch pass failed", str(exc)[:160], key="watch:failed"
            ))

        sent, resolved = self.dispatcher.dispatch(found)
        for alert in sent:
            record = {
                "severity": alert.severity, "title": alert.title,
                "detail": alert.detail, "key": alert.key, "at": _now(),
            }
            self.alerts.append(record)
            self.emit("alert", alert=record)
        for key in resolved:
            self.emit("resolved", key=key)

        self.scanning = ""
        self.last_pass_at = _now()
        self.next_pass_at = time.time() + max(60, self.config.interval_minutes * 60)
        self.emit("pass", snapshot=self.snapshot())

    def _absorb_catalog(self, result: agent.PassResult) -> None:
        report = result.audit_report
        if not report:
            return
        rank = {audit_mod.BLOCKER: 0, audit_mod.WARNING: 1, audit_mod.NOTE: 2}
        worst: dict[str, str] = {}
        problems: dict[str, list[str]] = {}
        for finding in report.findings:
            current = worst.get(finding.product)
            if current is None or rank[finding.severity] < rank[current]:
                worst[finding.product] = finding.severity
            problems.setdefault(finding.product, []).append(finding.problem)

        # Draw from the catalogue, not from the findings: a healthy product
        # produces no findings and must still appear in the workspace.
        self.products = [
            {
                "title": item["title"],
                "sku": item["sku"],
                "price": item["price"],
                "handle": item["handle"],
                "state": worst.get(item["title"], "ok"),
                "problems": problems.get(item["title"], []),
            }
            for item in result.catalog
        ]
        self.checks["catalogue"] = {
            "at": _now(),
            "checked": report.products_checked,
            "blockers": len(report.blockers),
        }

    def _absorb_orders(self, result: agent.PassResult) -> None:
        s = result.summary
        if s:
            self.summary = {
                "orders": s.orders, "revenue": round(s.revenue, 2),
                "currency": s.currency, "average": round(s.average_order, 2),
                "units": s.units, "needs_review": s.needs_review,
                "best": [{"title": t, "units": q} for t, q in s.by_product.most_common(5)],
            }
        self.checks["orders"] = {
            "at": _now(),
            "seen": len(result.order_views),
            "flagged": sum(1 for v in result.order_views if v.needs_review),
        }


def _handler(workspace: Workspace):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # Quieter than the default.
            log.debug("%s %s", self.address_string(), fmt % args)

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, name: str) -> None:
            # Resolve inside WEB_ROOT: a served path must never escape it.
            target = (WEB_ROOT / name).resolve()
            if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
                self.send_error(404)
                return
            data = target.read_bytes()
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            route = self.path.split("?")[0]
            if route not in ("/voice", "/voice/answer"):
                self.send_error(404)
                return

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(length, 64_000)).decode("utf-8", "replace")
            params = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

            # Without this check, anyone who found the public URL could ring
            # up and be read the shop's revenue.
            token = workspace.config.twilio.auth_token
            public = (workspace.config.voice_public_url or "").rstrip("/")
            expected_url = f"{public}{route}" if public else ""
            signature = self.headers.get("X-Twilio-Signature", "")
            if not voice_mod.verify(signature, expected_url, params, token):
                log.warning("refused an unverified call to %s", route)
                self._xml(voice_mod.rejected(), status=403)
                return

            if route == "/voice":
                self._xml(voice_mod.incoming_call())
            else:
                question = params.get("SpeechResult", "")
                log.info("call asked: %r", question)
                self._xml(voice_mod.spoken_answer(question, workspace.snapshot()))

        def _xml(self, body: str, status: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?")[0]
            if route in ("/", "/index.html"):
                self._file("index.html")
            elif route.startswith("/static/"):
                self._file(route[len("/static/"):])
            elif route == "/api/state":
                self._json(workspace.snapshot())
            elif route == "/api/events":
                self._events()
            else:
                self.send_error(404)

        def _events(self) -> None:
            q = workspace.listen()
            if q is None:
                self._json({"error": "too many listeners"}, status=503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self._write_event({"kind": "hello", "snapshot": workspace.snapshot()})
                while True:
                    try:
                        event = q.get(timeout=15)
                    except queue.Empty:
                        # A comment frame keeps proxies and tabs from timing out.
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    self._write_event(event)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                workspace.drop(q)

        def _write_event(self, event: dict) -> None:
            payload = json.dumps(event)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

    return Handler


def build(workspace: Workspace, port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(workspace))
    server.daemon_threads = True
    return server


def serve(
    config: Config,
    dispatcher: alerts_mod.Dispatcher,
    *,
    port: int = 8765,
    since_days: int = 7,
    browser_checks: bool = True,
    open_browser: bool = True,
) -> int:
    workspace = Workspace(config=config, dispatcher=dispatcher)
    server = build(workspace, port)
    host, bound = server.server_address[:2]
    url = f"http://127.0.0.1:{bound}/"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"workspace on {url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        while True:
            started = time.monotonic()
            workspace.run_pass(since_days=since_days, browser_checks=browser_checks)
            interval = max(60, config.interval_minutes * 60)
            time.sleep(max(5.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.shutdown()
    return 0
