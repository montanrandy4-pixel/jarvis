"""A scripted model backend, so the agent loop is testable offline."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.backends import Backend, BackendError, Completion, ToolCall  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.memory import MemoryStore  # noqa: E402


@dataclass
class FakeBackend(Backend):
    """Replays scripted completions and records every request it was given."""

    script: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    raise_once: Exception | None = None
    name: str = "fake"
    model: str = "fake-model"
    ready: bool = True

    def chat(self, messages, tools, *, on_text=None, cancel=None) -> Completion:
        self.calls.append({"messages": list(messages), "tools": tools})
        if self.raise_once is not None:
            error, self.raise_once = self.raise_once, None
            raise error
        if not self.script:
            raise AssertionError("the agent made more requests than were scripted")
        completion = self.script.pop(0)
        if cancel is not None and cancel.is_set():
            return Completion(finish_reason="cancelled")
        # Stream the text the way a real backend does, in small pieces.
        for index in range(0, len(completion.text), 7):
            if cancel is not None and cancel.is_set():
                return Completion(finish_reason="cancelled")
            if on_text:
                on_text(completion.text[index : index + 7])
        return completion

    def health(self) -> tuple[bool, str]:
        return self.ready, "fake backend"

    def models(self) -> list[str]:
        return ["fake-model"]


def says(text: str, finish_reason: str = "stop") -> Completion:
    return Completion(text=text, finish_reason=finish_reason)


def calls_tool(name: str, arguments: dict, *, text: str = "", call_id="call_1",
               raw: str = "") -> Completion:
    return Completion(
        text=text,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments, raw=raw)],
        finish_reason="tool_calls",
    )
