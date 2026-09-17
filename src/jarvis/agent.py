"""The conversation loop: a model, the tools, and the history between them.

Nothing here knows which model server is on the other end -- see
``jarvis.backends``.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .backends import Backend, BackendError, make_backend
from .persona import context_prompt, persona_prompt
from .tools import Registry

log = logging.getLogger("jarvis.agent")

MAX_TOOL_ITERATIONS = 8
# Rough bytes-per-token for trimming history. Deliberately pessimistic: running
# out of context mid-conversation is worse than trimming a turn early.
CHARS_PER_TOKEN = 3.2


@dataclass
class Turn:
    """What one exchange produced."""

    text: str = ""
    stop_reason: str = ""
    tool_calls: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class Agent:
    """Owns the message history and turns user utterances into replies."""

    def __init__(
        self,
        config,
        registry: Registry,
        memory,
        *,
        voice: bool = True,
        backend: Backend | None = None,
        on_tool=None,
    ):
        self.config = config
        self.registry = registry
        self.memory = memory
        self.voice = voice
        self.backend = backend or make_backend(config)
        self.on_tool = on_tool  # Called with (label, ToolResult) per tool run.
        self.messages: list[dict] = []
        self.started_at = time.time()
        self._persona = persona_prompt(
            name=config.name,
            address_as=config.address_user_as,
            user_name=config.user_name,
            voice=voice,
        )

    # -- prompt assembly -------------------------------------------------

    def _system_message(self) -> dict:
        """Persona plus the context that changes every turn."""
        content = self._persona + "\n\n" + context_prompt(
            memories=self.memory.texts()
        )
        return {"role": "system", "content": content}

    def _request_messages(self) -> list[dict]:
        return [self._system_message(), *self._trimmed()]

    def _trimmed(self) -> list[dict]:
        """Drop the oldest exchanges that no longer fit the context window."""
        budget = int(
            (self.config.context_tokens - self.config.max_reply_tokens)
            * CHARS_PER_TOKEN
        )
        budget -= len(self._persona) + 600  # Room for the system message.
        if budget <= 0:
            return self.messages[-2:]

        total = sum(_size(m) for m in self.messages)
        if total <= budget:
            return self.messages

        # Drop whole exchanges from the front. A tool result whose call has been
        # dropped confuses every backend, so cut at the next user message.
        start = 0
        while start < len(self.messages) and total > budget:
            total -= _size(self.messages[start])
            start += 1
            while (
                start < len(self.messages)
                and self.messages[start]["role"] != "user"
            ):
                total -= _size(self.messages[start])
                start += 1
        log.debug("trimmed %d old messages to fit the context window", start)
        return self.messages[start:]

    # -- the turn --------------------------------------------------------

    def reply(self, user_text: str, on_text=None, cancel=None) -> Turn:
        """Run one full turn, including any tool round-trips it needs."""
        self.messages.append({"role": "user", "content": user_text})
        turn = Turn()
        spoken: list[str] = []

        def collect(chunk: str) -> None:
            spoken.append(chunk)
            if on_text:
                on_text(chunk)

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                completion = self.backend.chat(
                    self._request_messages(),
                    self.registry.specs(),
                    on_text=collect,
                    cancel=cancel,
                )
            except BackendError as exc:
                turn.error = str(exc)
                turn.text = "".join(spoken)
                return turn

            if cancel is not None and cancel.is_set():
                turn.stop_reason = "cancelled"
                turn.text = "".join(spoken)
                return turn

            self.messages.append(_assistant_message(completion))
            turn.stop_reason = completion.finish_reason

            if completion.tool_calls:
                self.messages.extend(self._run_tools(completion.tool_calls, turn))
                continue

            if completion.finish_reason == "length":
                turn.text = "".join(spoken)
                turn.error = "I ran out of room mid-answer."
                return turn

            turn.text = "".join(spoken)
            return turn

        turn.text = "".join(spoken)
        turn.error = "I got stuck in a loop of tool calls and stopped."
        return turn

    def _run_tools(self, calls, turn: Turn) -> list[dict]:
        """Execute the model's tool calls, concurrently where there are several."""

        def execute(call):
            if call.name not in self.registry:
                known = ", ".join(sorted(self.registry.tools))
                return call, _error(f"No tool named {call.name!r}. Available: {known}")
            if call.raw and not call.arguments:
                return call, _error(
                    f"Could not parse the arguments for {call.name}: {call.raw[:200]}. "
                    "Send valid JSON."
                )
            return call, self.registry.run(call.name, call.arguments)

        if len(calls) == 1:
            outcomes = [execute(calls[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(calls))) as pool:
                outcomes = list(pool.map(execute, calls))

        messages = []
        for call, result in outcomes:
            tool = self.registry.tools.get(call.name)
            label = result.display or (
                tool.describe_call(call.arguments) if tool else call.name
            )
            turn.tool_calls.append(label)
            if self.on_tool:
                self.on_tool(label, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": result.content,
                }
            )
        return messages

    # -- housekeeping ----------------------------------------------------

    def reset(self) -> None:
        self.messages.clear()
        self.started_at = time.time()

    def save_transcript(self, directory: Path | None = None) -> Path | None:
        if not self.messages:
            return None
        directory = Path(directory or self.config.transcript_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started_at))
        path = directory / f"{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "backend": self.backend.name,
                    "model": self.backend.model,
                    "messages": self.messages,
                },
                indent=2,
                default=str,
            )
        )
        return path


def _assistant_message(completion) -> dict:
    message: dict = {"role": "assistant", "content": completion.text}
    if completion.tool_calls:
        message["tool_calls"] = [
            {"id": c.id, "name": c.name, "arguments": c.arguments}
            for c in completion.tool_calls
        ]
    return message


def _error(text: str):
    from .tools import ToolResult

    return ToolResult(text, is_error=True)


def _size(message: dict) -> int:
    return len(json.dumps(message, default=str))
