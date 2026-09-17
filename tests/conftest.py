"""A scripted stand-in for the Anthropic client, so the agent loop is testable."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# The Anthropic SDK moved from httpx to httpx2 in 1.0; tests build error
# responses with whichever one is installed.
try:  # pragma: no cover - depends on the installed SDK major version
    import httpx2 as http
except ImportError:  # pragma: no cover
    import httpx as http

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.config import Config  # noqa: E402
from jarvis.memory import MemoryStore  # noqa: E402


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict | None = None


@dataclass
class FakeMessage:
    content: list
    stop_reason: str = "end_turn"
    stop_details: object = None


@dataclass
class TextEvent:
    text: str
    type: str = "text"


@dataclass
class FakeStream:
    message: FakeMessage
    closed: bool = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.message.content:
            if block.type == "text":
                # Stream in small pieces, the way the API does.
                for i in range(0, len(block.text), 7):
                    yield TextEvent(block.text[i : i + 7])

    def get_final_message(self):
        return self.message

    def close(self):
        self.closed = True


@dataclass
class FakeMessages:
    """Replays a queue of scripted messages and records every request."""

    script: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    raise_once: Exception | None = None

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_once is not None:
            error, self.raise_once = self.raise_once, None
            raise error
        if not self.script:
            raise AssertionError("the agent made more requests than were scripted")
        item = self.script.pop(0)
        return FakeStream(item() if callable(item) else item)


class FakeClient:
    def __init__(self, script=None):
        self.beta = type("Beta", (), {})()
        self.beta.messages = FakeMessages(script=list(script or []))

    @property
    def calls(self):
        return self.beta.messages.calls


__all__ = ["http"]


def text_message(text: str, stop_reason: str = "end_turn") -> FakeMessage:
    return FakeMessage([Block("text", text=text)], stop_reason)


def tool_message(name: str, args: dict, *, text: str = "", block_id="tu_1") -> FakeMessage:
    content = []
    if text:
        content.append(Block("text", text=text))
    content.append(Block("tool_use", id=block_id, name=name, input=args))
    return FakeMessage(content, "tool_use")


@pytest.fixture
def config(tmp_path) -> Config:
    return Config.load(
        state_dir=tmp_path / "state",
        workspace=[str(tmp_path / "work")],
        shell="confirm",
        allow_web=False,
    )


@pytest.fixture
def memory(config) -> MemoryStore:
    return MemoryStore(config.memory_path)


@pytest.fixture
def workspace(config) -> Path:
    root = config.workspace_roots()[0]
    root.mkdir(parents=True, exist_ok=True)
    return root
