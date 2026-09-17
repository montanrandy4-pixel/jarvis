"""Model backends.

JARVIS does not depend on any particular model provider. A backend takes a
conversation plus a list of tools and streams back text and tool calls; the
agent loop above it does not care whether that came from a model running on
this machine or a server on the other side of the world.

Two are built in: Ollama (local, the default) and any OpenAI-compatible HTTP
endpoint (llama.cpp, vLLM, LM Studio, and the hosted services that speak the
same protocol).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ._http import HTTPError


class BackendError(RuntimeError):
    """Something went wrong that the user needs to hear about."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    # Kept for the error message when the model emits unparseable JSON.
    raw: str = ""


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # stop | length | tool_calls | cancelled
    finish_reason: str = "stop"


class Backend(ABC):
    """What the agent needs from a model server."""

    name = "backend"

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict], *, on_text=None,
             cancel=None) -> Completion:
        """Run one turn, streaming text to ``on_text`` as it is generated."""

    @abstractmethod
    def health(self) -> tuple[bool, str]:
        """Return (ready, human-readable detail) for `jarvis doctor`."""

    def models(self) -> list[str]:
        """Model names the server has available, if it can say."""
        return []

    @property
    def description(self) -> str:
        return f"{self.name} ({self.model})"


# Reasoning models wrap their scratchpad in tags. Speaking that aloud, or
# showing it in the transcript, is never what anyone wants.
THINK_TAGS = ("think", "thinking", "reasoning", "thought")
_OPEN = re.compile(r"<(" + "|".join(THINK_TAGS) + r")>", re.IGNORECASE)
_CLOSE = re.compile(r"</(" + "|".join(THINK_TAGS) + r")>", re.IGNORECASE)


class ThinkFilter:
    """Removes <think> blocks from a token stream, across chunk boundaries."""

    def __init__(self):
        self._inside = False
        self._pending = ""  # A partial tag split across two chunks.

    def feed(self, chunk: str) -> str:
        """Return only the part of ``chunk`` that should be shown or spoken."""
        text = self._pending + chunk
        self._pending = ""
        out = []
        while text:
            if self._inside:
                match = _CLOSE.search(text)
                if not match:
                    self._pending = _tail(text)
                    return "".join(out)
                self._inside = False
                text = text[match.end():]
                continue
            match = _OPEN.search(text)
            if not match:
                keep = _tail(text)
                out.append(text[: len(text) - len(keep)])
                self._pending = keep
                return "".join(out)
            out.append(text[: match.start()])
            self._inside = True
            text = text[match.end():]
        return "".join(out)

    def flush(self) -> str:
        """Whatever is left once the stream ends."""
        tail, self._pending = ("" if self._inside else self._pending), ""
        return tail

    @property
    def thinking(self) -> bool:
        return self._inside


def _tail(text: str) -> str:
    """Hold back a trailing fragment that might be the start of a tag."""
    cut = text.rfind("<")
    if cut == -1:
        return ""
    fragment = text[cut:]
    # Only hold it if it could still become a tag we care about.
    if ">" in fragment:
        return ""
    candidate = fragment.lstrip("<").lstrip("/").lower()
    if any(tag.startswith(candidate) for tag in THINK_TAGS):
        return fragment
    return ""


def parse_arguments(raw) -> tuple[dict, str]:
    """Normalise a tool call's arguments, which arrive as a dict or a string."""
    if isinstance(raw, dict):
        return raw, json.dumps(raw)
    text = (raw or "").strip()
    if not text:
        return {}, ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}, text
    return (parsed if isinstance(parsed, dict) else {"value": parsed}), text


def make_backend(config) -> Backend:
    """Build the backend named in the config."""
    from .ollama import OllamaBackend
    from .openai_compat import OpenAICompatibleBackend

    kind = (config.backend or "ollama").lower()
    if kind == "ollama":
        return OllamaBackend(config)
    if kind in {"openai", "openai-compatible", "compat"}:
        return OpenAICompatibleBackend(config)
    raise BackendError(
        f"Unknown backend {kind!r}. Use 'ollama' or 'openai'."
    )


__all__ = [
    "Backend",
    "BackendError",
    "Completion",
    "HTTPError",
    "ThinkFilter",
    "ToolCall",
    "make_backend",
    "parse_arguments",
]
