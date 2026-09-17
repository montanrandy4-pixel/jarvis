"""The model backends, exercised over real HTTP against a stub server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.backends import BackendError, ThinkFilter, parse_arguments
from jarvis.backends.ollama import OllamaBackend
from jarvis.backends.openai_compat import OpenAICompatibleBackend
from jarvis.config import Config


class StubServer:
    """A tiny model server that replays whatever the test hands it."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self):
                route = outer.routes.get(self.path)
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append(
                    {
                        "path": self.path,
                        "body": json.loads(body) if body else {},
                    }
                )
                if route is None:
                    self.send_error(404)
                    return
                status, payload = route
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(payload.encode())

            do_GET = _respond
            do_POST = _respond

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def route(self, path: str, payload: str, status: int = 200) -> None:
        self.routes[path] = (status, payload)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    server = StubServer()
    yield server
    server.stop()


def ndjson(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def sse(*events) -> str:
    lines = []
    for event in events:
        lines.append(
            "data: " + (event if isinstance(event, str) else json.dumps(event))
        )
    return "\n\n".join(lines) + "\n\n"


class TestOllama:
    def backend(self, stub, **overrides):
        config = Config.load(backend="ollama", base_url=stub.url, **overrides)
        return OllamaBackend(config)

    def test_streams_text_as_it_arrives(self, stub):
        stub.route(
            "/api/chat",
            ndjson(
                {"message": {"content": "Good "}},
                {"message": {"content": "evening."}},
                {"done": True, "done_reason": "stop"},
            ),
        )
        chunks = []

        completion = self.backend(stub).chat([], [], on_text=chunks.append)

        assert completion.text == "Good evening."
        assert chunks == ["Good ", "evening."]
        assert completion.finish_reason == "stop"

    def test_reasoning_is_not_streamed_to_the_user(self, stub):
        stub.route(
            "/api/chat",
            ndjson(
                {"message": {"content": "<think>the user wants"}},
                {"message": {"content": " the time</think>It is four."}},
                {"done": True},
            ),
        )
        chunks = []

        completion = self.backend(stub).chat([], [], on_text=chunks.append)

        assert completion.text == "It is four."
        assert "think" not in "".join(chunks)

    def test_tool_calls_are_returned(self, stub):
        stub.route(
            "/api/chat",
            ndjson(
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "set_timer",
                                    "arguments": {"seconds": 600},
                                }
                            }
                        ],
                    }
                },
                {"done": True},
            ),
        )

        completion = self.backend(stub).chat([], [])

        assert completion.finish_reason == "tool_calls"
        call = completion.tool_calls[0]
        assert call.name == "set_timer"
        assert call.arguments == {"seconds": 600}
        assert call.id  # the agent needs something to pair the result with

    def test_the_request_carries_model_tools_and_options(self, stub):
        stub.route("/api/chat", ndjson({"done": True}))
        tools = [{"type": "function", "function": {"name": "echo"}}]

        self.backend(stub, model="llama3.1:8b").chat(
            [{"role": "user", "content": "hi"}], tools
        )
        sent = stub.requests[-1]["body"]

        assert sent["model"] == "llama3.1:8b"
        assert sent["stream"] is True
        assert sent["tools"] == tools
        assert sent["options"]["num_ctx"] == 8192
        assert sent["keep_alive"]

    def test_tool_results_are_sent_in_ollama_shape(self, stub):
        stub.route("/api/chat", ndjson({"done": True}))

        self.backend(stub).chat(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "name": "echo", "arguments": {"value": "x"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "echo",
                    "content": "echoed x",
                },
            ],
            [],
        )
        messages = stub.requests[-1]["body"]["messages"]

        assert messages[0]["tool_calls"][0]["function"]["name"] == "echo"
        assert messages[1] == {
            "role": "tool",
            "content": "echoed x",
            "tool_name": "echo",
        }

    def test_an_error_event_becomes_a_backend_error(self, stub):
        stub.route("/api/chat", ndjson({"error": "model requires more system memory"}))

        with pytest.raises(BackendError, match="system memory"):
            self.backend(stub).chat([], [])

    def test_a_missing_server_explains_how_to_start_it(self):
        config = Config.load(backend="ollama", base_url="http://127.0.0.1:9")
        ready, detail = OllamaBackend(config).health()

        assert not ready
        assert "ollama serve" in detail

    def test_health_notices_an_uninstalled_model(self, stub):
        stub.route("/api/version", json.dumps({"version": "0.5.0"}))
        stub.route("/api/tags", json.dumps({"models": [{"name": "qwen2.5:7b"}]}))

        ready, detail = self.backend(stub, model="llama3.1:8b").health()

        assert not ready
        assert "ollama pull llama3.1:8b" in detail

    def test_health_passes_when_the_model_is_there(self, stub):
        stub.route("/api/version", json.dumps({"version": "0.5.0"}))
        stub.route("/api/tags", json.dumps({"models": [{"name": "llama3.1:8b"}]}))

        ready, _ = self.backend(stub, model="llama3.1:8b").health()

        assert ready

    def test_a_bare_model_name_matches_the_installed_tag(self, stub):
        stub.route("/api/version", json.dumps({"version": "0.5.0"}))
        stub.route("/api/tags", json.dumps({"models": [{"name": "llama3.1:latest"}]}))

        ready, _ = self.backend(stub, model="llama3.1").health()

        assert ready


class TestOpenAICompatible:
    def backend(self, stub, **overrides):
        config = Config.load(
            backend="openai", base_url=f"{stub.url}/v1", **overrides
        )
        return OpenAICompatibleBackend(config)

    def test_streams_text_from_sse(self, stub):
        stub.route(
            "/v1/chat/completions",
            sse(
                {"choices": [{"delta": {"content": "Two "}}]},
                {"choices": [{"delta": {"content": "degrees."}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                "[DONE]",
            ),
        )
        chunks = []

        completion = self.backend(stub).chat([], [], on_text=chunks.append)

        assert completion.text == "Two degrees."
        assert completion.finish_reason == "stop"

    def test_tool_arguments_spread_across_chunks_are_reassembled(self, stub):
        stub.route(
            "/v1/chat/completions",
            sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_9",
                                        "function": {
                                            "name": "set_timer",
                                            "arguments": '{"sec',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": 'onds": 60}'}}
                                ]
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                "[DONE]",
            ),
        )

        completion = self.backend(stub).chat([], [])

        call = completion.tool_calls[0]
        assert call.id == "call_9"
        assert call.arguments == {"seconds": 60}

    def test_tool_results_use_tool_call_ids(self, stub):
        stub.route("/v1/chat/completions", sse("[DONE]"))

        self.backend(stub).chat(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "name": "echo", "arguments": {"value": "x"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "name": "echo", "content": "ok"},
            ],
            [],
        )
        messages = stub.requests[-1]["body"]["messages"]

        assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]) == {
            "value": "x"
        }
        assert messages[1] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}

    def test_an_api_key_is_sent_when_one_is_configured(self, stub, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        stub.route("/v1/chat/completions", sse("[DONE]"))

        backend = self.backend(stub)

        assert backend._headers["Authorization"] == "Bearer sk-test"

    def test_a_rejected_key_is_explained(self, stub, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-wrong")
        stub.route("/v1/models", json.dumps({"error": "bad key"}), status=401)

        ready, detail = self.backend(stub).health()

        assert not ready
        assert "API key" in detail


class TestThinkFilter:
    def test_tags_split_across_chunks_are_still_removed(self):
        filter_ = ThinkFilter()

        out = "".join(
            filter_.feed(chunk)
            for chunk in ["<thi", "nk>hidden</thi", "nk>Visible."]
        )

        assert out + filter_.flush() == "Visible."

    def test_ordinary_angle_brackets_survive(self):
        filter_ = ThinkFilter()

        assert filter_.feed("5 < 6 and <b>bold</b>") == "5 < 6 and <b>bold</b>"

    def test_an_unclosed_block_hides_the_rest(self):
        filter_ = ThinkFilter()

        assert filter_.feed("<think>still reasoning") == ""
        assert filter_.flush() == ""


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"a": 1}, {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ("", {}),
        ("not json", {}),
    ],
)
def test_arguments_arrive_as_dicts_or_strings(raw, expected):
    assert parse_arguments(raw)[0] == expected
