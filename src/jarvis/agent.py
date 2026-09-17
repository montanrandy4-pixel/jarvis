"""The conversation loop: Claude, the tools, and the history between them."""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from .persona import context_prompt, persona_prompt
from .tools import Registry

log = logging.getLogger("jarvis.agent")

# Server-side features we ask for but can live without. If the account or SDK
# does not support one, the agent drops it and carries on rather than dying.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CONTEXT_BETA = "context-management-2025-06-27"

# Long voice sessions accumulate tool output nobody will refer to again; let the
# server clear the oldest of it instead of growing the prompt forever.
CLEAR_TOOL_USES = {"type": "clear_tool_uses_20250919"}

MAX_TOOL_ITERATIONS = 12


@dataclass
class Turn:
    """What one exchange produced."""

    text: str = ""
    stop_reason: str = ""
    tool_calls: list[str] = field(default_factory=list)
    refusal: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.refusal


class Agent:
    """Owns the message history and turns user utterances into replies."""

    def __init__(
        self,
        config,
        registry: Registry,
        memory,
        *,
        voice: bool = True,
        client=None,
        on_tool=None,
    ):
        self.config = config
        self.registry = registry
        self.memory = memory
        self.voice = voice
        self.client = client or anthropic.Anthropic()
        self.on_tool = on_tool  # Called with a human-readable line per tool run.
        self.messages: list[dict] = []
        self.started_at = time.time()

        self._persona = persona_prompt(
            name=config.name,
            address_as=config.address_user_as,
            user_name=config.user_name,
            voice=voice,
        )
        # Dropped on first rejection, so one unsupported beta cannot wedge the
        # assistant on every later turn.
        self._use_fallbacks = bool(config.server_fallbacks)
        self._use_context_editing = True

    # -- prompt assembly -------------------------------------------------

    def _system(self) -> list[dict]:
        """Stable persona first (cached), volatile context after the breakpoint."""
        return [
            {
                "type": "text",
                "text": self._persona,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": context_prompt(memories=self.memory.texts())},
        ]

    def _tools(self) -> list[dict]:
        specs = self.registry.specs()
        if self.config.allow_web:
            specs.append(
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": 5,
                }
            )
        return specs

    def _request_kwargs(self) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": self._system(),
            "messages": self.messages,
            "tools": self._tools(),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.config.effort},
        }
        betas: list[str] = []
        if self._use_fallbacks:
            betas.append(FALLBACK_BETA)
            kwargs["fallbacks"] = "default"
        if self._use_context_editing:
            betas.append(CONTEXT_BETA)
            kwargs["context_management"] = {"edits": [CLEAR_TOOL_USES]}
        if betas:
            kwargs["betas"] = betas
        return kwargs

    def _degrade(self, exc: Exception) -> bool:
        """Turn off whichever optional feature the API just rejected."""
        detail = str(exc).lower()
        if self._use_fallbacks and (
            "fallback" in detail or FALLBACK_BETA in detail
        ):
            log.warning("server-side fallbacks unavailable; continuing without")
            self._use_fallbacks = False
            return True
        if self._use_context_editing and (
            "context_management" in detail or "context-management" in detail
        ):
            log.warning("context editing unavailable; continuing without")
            self._use_context_editing = False
            return True
        # An unrecognised 400 might still be one of ours; drop both once.
        if self._use_fallbacks or self._use_context_editing:
            log.warning("request rejected (%s); retrying without beta features", exc)
            self._use_fallbacks = False
            self._use_context_editing = False
            return True
        return False

    # -- the turn --------------------------------------------------------

    def reply(self, user_text: str, on_text=None, cancel=None) -> Turn:
        """Run one full turn, including any tool round-trips it needs.

        ``on_text`` receives text as it streams, so a voice front end can start
        speaking the first sentence while the rest is still arriving.
        ``cancel`` is an Event; setting it stops generation (barge-in).
        """
        self.messages.append({"role": "user", "content": user_text})
        turn = Turn()
        spoken: list[str] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                message = self._stream_once(on_text, cancel, spoken)
            except anthropic.APIStatusError as exc:
                if exc.status_code == 400 and self._degrade(exc):
                    continue
                turn.error = _friendly_error(exc)
                return turn
            except anthropic.APIConnectionError:
                turn.error = "I can't reach the network just now."
                return turn
            except TypeError as exc:
                # The SDK raises this when it cannot resolve any credential.
                if "authentication" not in str(exc).lower():
                    raise
                turn.error = (
                    "I have no API credentials. Set ANTHROPIC_API_KEY or run "
                    "`ant auth login`."
                )
                return turn

            if message is None:  # Cancelled mid-stream by barge-in.
                turn.stop_reason = "cancelled"
                turn.text = "".join(spoken)
                return turn

            self.messages.append(
                {"role": "assistant", "content": message.content}
            )
            turn.stop_reason = message.stop_reason or ""

            if message.stop_reason == "refusal":
                details = getattr(message, "stop_details", None)
                turn.refusal = getattr(details, "explanation", "") or (
                    "I'm not able to help with that one."
                )
                turn.text = "".join(spoken)
                return turn

            if message.stop_reason == "max_tokens":
                turn.text = "".join(spoken)
                turn.error = "I ran out of room mid-answer."
                return turn

            if message.stop_reason == "pause_turn":
                # A server-side tool wants to keep going; send the turn back.
                continue

            if message.stop_reason == "tool_use":
                results = self._run_tools(message, turn)
                self.messages.append({"role": "user", "content": results})
                continue

            turn.text = "".join(spoken)
            return turn

        turn.text = "".join(spoken)
        turn.error = "I got stuck in a loop of tool calls and stopped."
        return turn

    def _stream_once(self, on_text, cancel, spoken: list[str]):
        """One streaming request. Returns None if cancelled part-way."""
        kwargs = self._request_kwargs()
        with self.client.beta.messages.stream(**kwargs) as stream:
            for event in stream:
                if cancel is not None and cancel.is_set():
                    stream.close()
                    return None
                if event.type == "text":
                    chunk = event.text
                    spoken.append(chunk)
                    if on_text:
                        on_text(chunk)
            return stream.get_final_message()

    def _run_tools(self, message, turn: Turn) -> list[dict]:
        """Execute every tool_use block in the message, concurrently."""
        calls = [
            block
            for block in message.content
            if getattr(block, "type", "") == "tool_use"
            and block.name in self.registry
        ]
        if not calls:
            # A tool_use stop with no client tool means a server tool ran; there
            # is nothing for us to return.
            return [
                {
                    "type": "text",
                    "text": "(no client-side tool results)",
                }
            ]

        def execute(block):
            return block, self.registry.run(block.name, block.input)

        if len(calls) == 1:
            outcomes = [execute(calls[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(calls))) as pool:
                outcomes = list(pool.map(execute, calls))

        results = []
        for block, result in outcomes:
            label = result.display or self.registry.tools[block.name].describe_call(
                block.input if isinstance(block.input, dict) else {}
            )
            turn.tool_calls.append(label)
            if self.on_tool:
                self.on_tool(label, result)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
            )
        return results

    # -- housekeeping ----------------------------------------------------

    def reset(self) -> None:
        self.messages.clear()
        self.started_at = time.time()

    def save_transcript(self, directory: Path | None = None) -> Path | None:
        """Write the conversation to disk so it can be read back later."""
        if not self.messages:
            return None
        directory = Path(directory or self.config.transcript_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started_at))
        path = directory / f"{stamp}.json"
        path.write_text(
            json.dumps(
                {"model": self.config.model, "messages": self.messages},
                indent=2,
                default=str,
            )
        )
        return path


def _friendly_error(exc: anthropic.APIStatusError) -> str:
    """Something a voice assistant can say out loud without embarrassment."""
    if isinstance(exc, anthropic.AuthenticationError):
        return "My API key is not being accepted."
    if isinstance(exc, anthropic.RateLimitError):
        return "I'm being rate limited. Give me a moment."
    if isinstance(exc, anthropic.NotFoundError):
        return "That model isn't available on this account."
    if exc.status_code >= 500:
        return "The API is having trouble. Try again shortly."
    return f"The request failed: {exc}"
