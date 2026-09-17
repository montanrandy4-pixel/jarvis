"""The app: HTTP endpoints, streaming and the permission round trip."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest
from conftest import FakeBackend, calls_tool, says

from jarvis.server import build_server


@pytest.fixture
def app(config, monkeypatch):
    """A running app on an ephemeral port, backed by a scripted model."""
    config.port = 0
    config.shell = "confirm"
    backend = FakeBackend(script=[])
    server, session = build_server(config, backend=backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield type(
        "App",
        (),
        {
            "url": f"http://{host}:{port}",
            "backend": backend,
            "session": session,
            "config": config,
        },
    )
    server.shutdown()
    server.server_close()


def get(app, path: str):
    with urllib.request.urlopen(f"{app.url}{path}", timeout=5) as response:
        return response.status, response.read().decode()


def post(app, path: str, payload: dict):
    request = urllib.request.Request(
        f"{app.url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read().decode()


def stream(app, text: str, timeout: float = 10.0):
    """POST a message and collect the server-sent events it streams back."""
    request = urllib.request.Request(
        f"{app.url}/api/chat",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            decoded = line.decode().strip()
            if decoded.startswith("data:"):
                events.append(json.loads(decoded[5:]))
    return events


class TestPages:
    def test_the_app_page_is_served(self, app):
        status, body = get(app, "/")

        assert status == 200
        assert "<title>JARVIS</title>" in body

    def test_assets_are_served(self, app):
        assert get(app, "/static/app.js")[0] == 200
        assert get(app, "/static/styles.css")[0] == 200

    def test_the_static_route_cannot_escape_the_app(self, app):
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(app, "/static/../server.py")

        assert caught.value.code == 404


class TestState:
    def test_state_describes_the_assistant(self, app):
        _, body = get(app, "/api/state")
        state = json.loads(body)

        assert state["model"] == "fake-model"
        assert state["ready"] is True
        assert "set_timer" in state["tools"]
        assert state["wake_word"] == "hey jarvis"

    def test_memories_are_listed_and_can_be_forgotten(self, app):
        item = app.session.memory.add("The user's car is a blue Volvo.")

        state = json.loads(get(app, "/api/state")[1])
        assert state["memories"][0]["text"].startswith("The user's car")

        post(app, "/api/forget", {"id": item.id})

        assert json.loads(get(app, "/api/state")[1])["memories"] == []


class TestConversation:
    def test_a_reply_streams_back_then_finishes(self, app):
        app.backend.script = [says("The kettle is on.")]

        events = stream(app, "put the kettle on")

        text = "".join(e["value"] for e in events if e["type"] == "text")
        assert text == "The kettle is on."
        assert events[-1]["type"] == "done"
        assert events[-1]["error"] == ""

    def test_tool_use_is_reported_to_the_browser(self, app):
        app.backend.script = [
            calls_tool("set_timer", {"seconds": 300, "label": "eggs"}),
            says("Timer set."),
        ]

        events = stream(app, "five minute timer for the eggs")

        tools = [e for e in events if e["type"] == "tool"]
        assert tools and "5 minutes" in tools[0]["label"]
        assert events[-1]["text"] == "Timer set."

    def test_backend_failures_reach_the_user(self, app):
        from jarvis.backends import BackendError

        app.backend.raise_once = BackendError("Ollama is not running.")

        events = stream(app, "hello")

        assert events[-1]["type"] == "done"
        assert "Ollama is not running." in events[-1]["error"]

    def test_an_empty_message_is_rejected(self, app):
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(app, "/api/chat", {"text": "   "})

        assert caught.value.code == 400

    def test_one_turns_events_never_leak_into_the_next(self, app):
        # Each turn gets its own queue; a straggling event from the first reply
        # must not appear at the start of the second.
        app.backend.script = [says("The first answer."), says("The second answer.")]

        stream(app, "first question")
        events = stream(app, "second question")

        text = "".join(e["value"] for e in events if e["type"] == "text")
        assert text == "The second answer."
        assert "first" not in text.lower()

    def test_a_timer_firing_between_turns_is_announced(self, app):
        app.session.emit({"type": "announce", "text": "Your timer is up."})

        _, body = get(app, "/api/events")

        assert json.loads(body)["events"][0]["text"] == "Your timer is up."

    def test_reset_clears_the_conversation(self, app):
        app.backend.script = [says("Hello.")]
        stream(app, "hello")

        post(app, "/api/reset", {})

        assert app.session.agent.messages == []


class TestPermission:
    def test_a_dangerous_tool_waits_for_the_user_and_can_be_allowed(self, app, tmp_path):
        target = tmp_path / "work" / "note.txt"
        app.backend.script = [
            calls_tool("write_file", {"path": str(target), "content": "done"}),
            says("Written."),
        ]
        answered = threading.Event()

        def answer_when_asked():
            # Stand in for the user clicking "Allow" in the browser.
            for _ in range(100):
                pending = list(app.session._pending)
                if pending:
                    post(app, "/api/confirm", {"id": pending[0], "allow": True})
                    answered.set()
                    return
                threading.Event().wait(0.05)

        threading.Thread(target=answer_when_asked, daemon=True).start()
        events = stream(app, "write a note")

        assert answered.is_set()
        assert any(e["type"] == "confirm" for e in events)
        assert target.read_text() == "done"
        assert events[-1]["text"] == "Written."

    def test_denying_stops_the_tool(self, app, tmp_path):
        target = tmp_path / "work" / "nope.txt"
        app.backend.script = [
            calls_tool("write_file", {"path": str(target), "content": "no"}),
            says("Understood."),
        ]

        def refuse():
            for _ in range(100):
                pending = list(app.session._pending)
                if pending:
                    post(app, "/api/confirm", {"id": pending[0], "allow": False})
                    return
                threading.Event().wait(0.05)

        threading.Thread(target=refuse, daemon=True).start()
        stream(app, "write a note")

        assert not target.exists()

    def test_read_only_work_never_asks(self, app):
        app.backend.script = [
            calls_tool("run_shell", {"command": "echo hello"}),
            says("It said hello."),
        ]

        events = stream(app, "run echo")

        assert not any(e["type"] == "confirm" for e in events)
        assert events[-1]["text"] == "It said hello."
