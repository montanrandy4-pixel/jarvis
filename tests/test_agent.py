"""The conversation loop: streaming, tool round-trips and failure handling."""

from __future__ import annotations

import threading

import anthropic
import pytest
from conftest import Block, FakeClient, FakeMessage, http, text_message, tool_message

from jarvis.agent import CONTEXT_BETA, FALLBACK_BETA, Agent
from jarvis.tools import Registry, Tool, ToolResult


def make_agent(config, memory, script, **kwargs) -> Agent:
    registry = kwargs.pop("registry", None)
    if registry is None:
        registry = Registry()
        registry.add(
            Tool(
                name="echo",
                description="echo",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                handler=lambda value: ToolResult(f"echoed {value}"),
            )
        )
    return Agent(
        config, registry, memory, client=FakeClient(script), **kwargs
    )


def test_plain_reply_streams_and_records_history(config, memory):
    agent = make_agent(config, memory, [text_message("All systems nominal.")])
    chunks: list[str] = []

    turn = agent.reply("status report", on_text=chunks.append)

    assert turn.text == "All systems nominal."
    assert turn.ok and turn.stop_reason == "end_turn"
    assert "".join(chunks) == "All systems nominal."  # arrived incrementally
    assert len(chunks) > 1
    assert agent.messages[0] == {"role": "user", "content": "status report"}
    assert agent.messages[1]["role"] == "assistant"


def test_tool_call_round_trip(config, memory):
    agent = make_agent(
        config,
        memory,
        [tool_message("echo", {"value": "hello"}), text_message("Done.")],
    )

    turn = agent.reply("echo hello")

    assert turn.text == "Done."
    assert turn.tool_calls == ["echo"]
    # The tool result must go back as a user message keyed to the tool_use id.
    result_message = agent.messages[2]
    assert result_message["role"] == "user"
    block = result_message["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_1"
    assert block["content"] == "echoed hello"
    assert block["is_error"] is False


def test_parallel_tool_calls_return_in_one_message(config, memory):
    message = FakeMessage(
        [
            Block("tool_use", id="a", name="echo", input={"value": "one"}),
            Block("tool_use", id="b", name="echo", input={"value": "two"}),
        ],
        "tool_use",
    )
    agent = make_agent(config, memory, [message, text_message("Both done.")])

    agent.reply("echo twice")

    results = agent.messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert len(agent.client.calls) == 2


def test_invalid_tool_input_is_reported_not_raised(config, memory):
    # An eagerly streamed input can arrive truncated; the agent must hand the
    # model a correctable error instead of crashing.
    agent = make_agent(
        config,
        memory,
        [tool_message("echo", {}), text_message("Sorry, retried.")],
    )

    turn = agent.reply("echo nothing")

    block = agent.messages[2]["content"][0]
    assert block["is_error"] is True
    assert "missing required field" in block["content"]
    assert turn.text == "Sorry, retried."


def test_unknown_tool_does_not_wedge_the_turn(config, memory):
    message = FakeMessage(
        [Block("tool_use", id="x", name="nonexistent", input={})], "tool_use"
    )
    agent = make_agent(config, memory, [message, text_message("Moving on.")])

    assert agent.reply("do the thing").text == "Moving on."


def test_refusal_is_surfaced_without_pretending_to_answer(config, memory):
    details = type("Details", (), {"type": "refusal", "explanation": "Not that one."})()
    agent = make_agent(config, memory, [FakeMessage([], "refusal", details)])

    turn = agent.reply("something off limits")

    assert turn.refusal == "Not that one."
    assert not turn.ok


def test_max_tokens_reports_truncation(config, memory):
    agent = make_agent(config, memory, [text_message("I was saying", "max_tokens")])

    turn = agent.reply("go on")

    assert turn.text == "I was saying"
    assert "ran out of room" in turn.error


def test_pause_turn_continues_the_request(config, memory):
    agent = make_agent(
        config,
        memory,
        [FakeMessage([Block("text", text="Looking. ")], "pause_turn"),
         text_message("Found it.")],
    )

    turn = agent.reply("search the web")

    assert turn.text == "Looking. Found it."
    assert len(agent.client.calls) == 2


def test_cancel_stops_generation_mid_stream(config, memory):
    agent = make_agent(config, memory, [text_message("A very long answer indeed.")])
    cancel = threading.Event()
    cancel.set()  # Barge-in before the first event is consumed.

    turn = agent.reply("never mind", cancel=cancel)

    assert turn.stop_reason == "cancelled"


def test_tool_loop_has_a_ceiling(config, memory):
    script = [tool_message("echo", {"value": "again"}) for _ in range(20)]
    agent = make_agent(config, memory, script)

    turn = agent.reply("loop forever")

    assert "stuck in a loop" in turn.error


def _bad_request(message: str) -> anthropic.BadRequestError:
    request = http.Request("POST", "https://api.anthropic.com/v1/messages")
    response = http.Response(400, request=request, json={"error": {"message": message}})
    return anthropic.BadRequestError(message, response=response, body=None)


def test_unsupported_beta_is_dropped_and_retried(config, memory):
    agent = make_agent(config, memory, [text_message("Back on track.")])
    agent.client.beta.messages.raise_once = _bad_request(
        "unsupported beta: server-side-fallback-2026-07-01"
    )

    turn = agent.reply("hello")

    assert turn.text == "Back on track."
    assert "fallbacks" in agent.client.calls[0]
    assert "fallbacks" not in agent.client.calls[1]  # dropped on the retry
    assert FALLBACK_BETA not in agent.client.calls[1].get("betas", [])


def test_api_errors_become_something_sayable(config, memory):
    agent = make_agent(config, memory, [])
    request = http.Request("POST", "https://api.anthropic.com/v1/messages")
    agent.client.beta.messages.raise_once = anthropic.RateLimitError(
        "slow down", response=http.Response(429, request=request), body=None
    )

    turn = agent.reply("hello")

    assert "rate limited" in turn.error.lower()
    assert not turn.ok


def test_request_shape_is_cache_friendly(config, memory):
    agent = make_agent(config, memory, [text_message("ok")])

    agent.reply("hello")
    kwargs = agent.client.calls[0]

    assert kwargs["model"] == config.model
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": config.effort}
    assert set(kwargs["betas"]) == {FALLBACK_BETA, CONTEXT_BETA}
    # Stable persona is cached; the volatile context block sits after it.
    system = kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]
    assert "Current context" in system[1]["text"]
    assert all(tool["eager_input_streaming"] for tool in kwargs["tools"])


def test_memories_reach_the_prompt(config, memory):
    memory.add("The user prefers metric units.")
    agent = make_agent(config, memory, [text_message("Noted.")])

    agent.reply("how far is it")

    assert "metric units" in agent.client.calls[0]["system"][1]["text"]


def test_transcript_is_saved(config, memory):
    agent = make_agent(config, memory, [text_message("Saved.")])
    agent.reply("remember this")

    path = agent.save_transcript()

    assert path.exists()
    assert "remember this" in path.read_text()


def test_missing_credentials_are_explained_not_traced(config, memory):
    agent = make_agent(config, memory, [])
    agent.client.beta.messages.raise_once = TypeError(
        "Could not resolve authentication method. Expected one of api_key, ..."
    )

    turn = agent.reply("hello")

    assert "ANTHROPIC_API_KEY" in turn.error
    assert not turn.ok


def test_unrelated_type_errors_still_surface(config, memory):
    agent = make_agent(config, memory, [])
    agent.client.beta.messages.raise_once = TypeError("a real bug")

    with pytest.raises(TypeError, match="a real bug"):
        agent.reply("hello")
