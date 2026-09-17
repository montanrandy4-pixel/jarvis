"""Ollama backend -- a model running on this machine.

Speaks Ollama's native /api/chat protocol, which streams NDJSON: one JSON
object per line, each carrying a fragment of the reply.
"""

from __future__ import annotations

import json
import logging

from . import Backend, BackendError, Completion, ThinkFilter, ToolCall, parse_arguments
from ._http import HTTPError, request_json, stream_lines

log = logging.getLogger("jarvis.ollama")


class OllamaBackend(Backend):
    name = "ollama"

    def __init__(self, config):
        super().__init__(config.model)
        self.base_url = (config.base_url or "http://localhost:11434").rstrip("/")
        self.temperature = config.temperature
        self.context_tokens = config.context_tokens
        self.max_reply_tokens = config.max_reply_tokens
        # Keep the model resident between utterances; reloading an 8B model on
        # every question adds seconds to each reply.
        self.keep_alive = config.keep_alive

    def chat(self, messages, tools, *, on_text=None, cancel=None) -> Completion:
        payload = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_tokens,
                "num_predict": self.max_reply_tokens,
            },
        }
        if tools:
            payload["tools"] = tools

        completion = Completion()
        filter_ = ThinkFilter()
        text: list[str] = []
        try:
            for line in stream_lines(
                f"{self.base_url}/api/chat", payload, cancel=cancel
            ):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("ignoring unparseable line: %s", line[:200])
                    continue
                if event.get("error"):
                    raise BackendError(str(event["error"]))

                message = event.get("message") or {}
                chunk = message.get("content") or ""
                if chunk:
                    visible = filter_.feed(chunk)
                    if visible:
                        text.append(visible)
                        if on_text:
                            on_text(visible)
                for call in message.get("tool_calls") or []:
                    completion.tool_calls.append(_to_tool_call(call))
                if event.get("done"):
                    completion.finish_reason = _finish(event.get("done_reason"))
        except HTTPError as exc:
            raise BackendError(self._explain(exc)) from exc

        tail = filter_.flush()
        if tail:
            text.append(tail)
            if on_text:
                on_text(tail)

        if cancel is not None and cancel.is_set():
            completion.finish_reason = "cancelled"
        elif completion.tool_calls:
            completion.finish_reason = "tool_calls"
        completion.text = "".join(text)
        return completion

    def health(self) -> tuple[bool, str]:
        try:
            version = request_json(f"{self.base_url}/api/version", timeout=5)
        except HTTPError as exc:
            return False, self._explain(exc)
        installed = self.models()
        if installed and not _has_model(self.model, installed):
            return False, (
                f"Ollama {version.get('version', '')} is running, but "
                f"{self.model!r} is not installed. Pull it with: "
                f"ollama pull {self.model}"
            )
        return True, f"Ollama {version.get('version', '?')} at {self.base_url}"

    def models(self) -> list[str]:
        try:
            data = request_json(f"{self.base_url}/api/tags", timeout=5)
        except HTTPError:
            return []
        return sorted(m.get("name", "") for m in data.get("models", []))

    def supports_tools(self) -> bool | None:
        """Whether the model advertises tool support. None if unknown."""
        try:
            info = request_json(
                f"{self.base_url}/api/show",
                {"model": self.model},
                method="POST",
                timeout=10,
            )
        except HTTPError:
            return None
        capabilities = info.get("capabilities")
        if not isinstance(capabilities, list):
            return None
        return "tools" in capabilities

    def _explain(self, exc: HTTPError) -> str:
        detail = str(exc)
        if "connection refused" in detail or "could not reach" in detail:
            return (
                f"Ollama is not running at {self.base_url}. "
                "Start it with: ollama serve"
            )
        if "404" in detail and "model" in detail.lower():
            return f"Model {self.model!r} is not installed. Run: ollama pull {self.model}"
        return detail


def _to_wire(message: dict) -> dict:
    """Convert the agent's neutral message format to Ollama's."""
    role = message["role"]
    if role == "tool":
        return {
            "role": "tool",
            "content": message["content"],
            "tool_name": message.get("name", ""),
        }
    out: dict = {"role": role, "content": message.get("content") or ""}
    calls = message.get("tool_calls")
    if calls:
        out["tool_calls"] = [
            {"function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls
        ]
    return out


def _to_tool_call(call: dict) -> ToolCall:
    function = call.get("function") or {}
    arguments, raw = parse_arguments(function.get("arguments"))
    return ToolCall(
        # Ollama does not issue call ids; the agent only needs them to pair
        # results with calls, so a positional one is enough.
        id=call.get("id") or f"call_{function.get('name', 'tool')}",
        name=function.get("name", ""),
        arguments=arguments,
        raw=raw,
    )


def _finish(reason: str | None) -> str:
    return {"stop": "stop", "length": "length", None: "stop"}.get(reason, reason or "stop")


def _has_model(wanted: str, installed: list[str]) -> bool:
    # "llama3.1:8b" should match an installed "llama3.1:8b"; "llama3.1" should
    # match "llama3.1:latest" too.
    if wanted in installed:
        return True
    base = wanted.split(":")[0]
    return any(name.split(":")[0] == base for name in installed)
