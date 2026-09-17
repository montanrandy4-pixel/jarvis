"""Any server that speaks the OpenAI chat-completions protocol.

That covers llama.cpp's server, vLLM, LM Studio, text-generation-webui and the
hosted services built on the same shape. Point ``base_url`` at it and, if it
wants one, set an API key.
"""

from __future__ import annotations

import json
import logging
import os

from . import Backend, BackendError, Completion, ThinkFilter, ToolCall, parse_arguments
from ._http import HTTPError, request_json, stream_lines

log = logging.getLogger("jarvis.openai")


class OpenAICompatibleBackend(Backend):
    name = "openai-compatible"

    def __init__(self, config):
        super().__init__(config.model)
        self.base_url = (config.base_url or "http://localhost:8080/v1").rstrip("/")
        self.temperature = config.temperature
        self.max_reply_tokens = config.max_reply_tokens
        self.api_key = os.environ.get(config.api_key_env, "")

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def chat(self, messages, tools, *, on_text=None, cancel=None) -> Completion:
        payload = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": self.max_reply_tokens,
        }
        if tools:
            payload["tools"] = tools

        completion = Completion()
        filter_ = ThinkFilter()
        text: list[str] = []
        # Tool call arguments stream in as fragments keyed by index.
        pending: dict[int, dict] = {}
        try:
            for line in stream_lines(
                f"{self.base_url}/chat/completions",
                payload,
                headers=self._headers,
                cancel=cancel,
            ):
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    raise BackendError(_error_text(event["error"]))

                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    chunk = delta.get("content") or ""
                    if chunk:
                        visible = filter_.feed(chunk)
                        if visible:
                            text.append(visible)
                            if on_text:
                                on_text(visible)
                    for fragment in delta.get("tool_calls") or []:
                        _accumulate(pending, fragment)
                    if choice.get("finish_reason"):
                        completion.finish_reason = choice["finish_reason"]
        except HTTPError as exc:
            raise BackendError(self._explain(exc)) from exc

        tail = filter_.flush()
        if tail:
            text.append(tail)
            if on_text:
                on_text(tail)

        completion.tool_calls = [_to_tool_call(p) for p in _in_order(pending)]
        if cancel is not None and cancel.is_set():
            completion.finish_reason = "cancelled"
        elif completion.tool_calls:
            completion.finish_reason = "tool_calls"
        completion.text = "".join(text)
        return completion

    def health(self) -> tuple[bool, str]:
        try:
            request_json(f"{self.base_url}/models", headers=self._headers, timeout=10)
        except HTTPError as exc:
            return False, self._explain(exc)
        return True, f"{self.base_url}"

    def models(self) -> list[str]:
        try:
            data = request_json(
                f"{self.base_url}/models", headers=self._headers, timeout=10
            )
        except HTTPError:
            return []
        return sorted(m.get("id", "") for m in data.get("data", []))

    def _explain(self, exc: HTTPError) -> str:
        detail = str(exc)
        if "connection refused" in detail or "could not reach" in detail:
            return f"No server is answering at {self.base_url}."
        if "401" in detail or "403" in detail:
            return "The server rejected the API key."
        return detail


def _to_wire(message: dict) -> dict:
    role = message["role"]
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.get("tool_call_id", ""),
            "content": message["content"],
        }
    out: dict = {"role": role, "content": message.get("content") or ""}
    calls = message.get("tool_calls")
    if calls:
        out["tool_calls"] = [
            {
                "id": c["id"],
                "type": "function",
                "function": {
                    "name": c["name"],
                    "arguments": json.dumps(c["arguments"]),
                },
            }
            for c in calls
        ]
    return out


def _accumulate(pending: dict[int, dict], fragment: dict) -> None:
    """Merge a streamed tool-call fragment into the call being assembled."""
    index = fragment.get("index", 0)
    slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if fragment.get("id"):
        slot["id"] = fragment["id"]
    function = fragment.get("function") or {}
    if function.get("name"):
        slot["name"] = function["name"]
    if function.get("arguments"):
        slot["arguments"] += function["arguments"]


def _in_order(pending: dict[int, dict]) -> list[dict]:
    return [pending[key] for key in sorted(pending)]


def _to_tool_call(slot: dict) -> ToolCall:
    arguments, raw = parse_arguments(slot["arguments"])
    return ToolCall(
        id=slot["id"] or f"call_{slot['name']}",
        name=slot["name"],
        arguments=arguments,
        raw=raw,
    )


def _error_text(error) -> str:
    if isinstance(error, dict):
        return str(error.get("message", error))
    return str(error)
