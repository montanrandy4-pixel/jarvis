"""The conversation loop: streaming, tool round-trips and failure handling."""

from __future__ import annotations

import threading

import pytest
from conftest import BackendError, Completion, FakeBackend, ToolCall, calls_tool, says

from jarvis.agent import Agent
from jarvis.tools import Registry, Tool, ToolResult


def echo_registry() -> Registry:
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
    return registry


def make_agent(config, memory, script, registry=None, **kwargs) -> Agent:
    return Agent(
        config,
        registry or echo_registry(),
        memory,
        backend=FakeBackend(script=list(script)),
        **kwargs,
    )


def test_plain_reply_streams_and_records_history(config, memory):
    agent = make_agent(config, memory, [says("All systems nominal.")])
    chunks: list[str] = []

    turn = agent.reply("status report", on_text=chunks.append)

    assert turn.text == "All systems nominal."
    assert turn.ok and turn.stop_reason == "stop"
    assert "".join(chunks) == "All systems nominal."
    assert len(chunks) > 1  # arrived incrementally
    assert agent.messages[0] == {"role": "user", "content": "status report"}
    assert agent.messages[1]["role"] == "assistant"


def test_tool_call_round_trip(config, memory):
    agent = make_agent(
        config, memory, [calls_tool("echo", {"value": "hello"}), says("Done.")]
    )

    turn = agent.reply("echo hello")

    assert turn.text == "Done."
    assert turn.tool_calls == ["echo"]
    assistant, result = agent.messages[1], agent.messages[2]
    assert assistant["tool_calls"][0]["name"] == "echo"
    assert result == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "echo",
        "content": "echoed hello",
    }


def test_parallel_tool_calls_all_get_results(config, memory):
    completion = Completion(
        tool_calls=[
            ToolCall(id="a", name="echo", arguments={"value": "one"}),
            ToolCall(id="b", name="echo", arguments={"value": "two"}),
        ],
        finish_reason="tool_calls",
    )
    agent = make_agent(config, memory, [completion, says("Both done.")])

    agent.reply("echo twice")

    results = [m for m in agent.messages if m["role"] == "tool"]
    assert [r["tool_call_id"] for r in results] == ["a", "b"]
    assert len(agent.backend.calls) == 2


def test_invalid_tool_input_is_reported_not_raised(config, memory):
    agent = make_agent(config, memory, [calls_tool("echo", {}), says("Retried.")])

    turn = agent.reply("echo nothing")

    result = [m for m in agent.messages if m["role"] == "tool"][0]
    assert "missing required field" in result["content"]
    assert turn.text == "Retried."


def test_unparseable_arguments_are_explained_to_the_model(config, memory):
    # Small models sometimes emit malformed JSON for tool arguments.
    agent = make_agent(
        config,
        memory,
        [calls_tool("echo", {}, raw='{"value": "hal'), says("Fixed.")],
    )

    agent.reply("echo hello")

    result = [m for m in agent.messages if m["role"] == "tool"][0]
    assert "Could not parse the arguments" in result["content"]


def test_a_hallucinated_tool_gets_the_real_list_back(config, memory):
    completion = Completion(
        tool_calls=[ToolCall(id="x", name="send_email", arguments={})],
        finish_reason="tool_calls",
    )
    agent = make_agent(config, memory, [completion, says("Moving on.")])

    turn = agent.reply("email my mother")

    result = [m for m in agent.messages if m["role"] == "tool"][0]
    assert "No tool named 'send_email'" in result["content"]
    assert "echo" in result["content"]  # tells it what it can use
    assert turn.text == "Moving on."


def test_running_out_of_room_is_reported(config, memory):
    agent = make_agent(config, memory, [says("I was saying", "length")])

    turn = agent.reply("go on")

    assert turn.text == "I was saying"
    assert "ran out of room" in turn.error


def test_cancel_stops_generation(config, memory):
    agent = make_agent(config, memory, [says("A very long answer indeed.")])
    cancel = threading.Event()
    cancel.set()

    turn = agent.reply("never mind", cancel=cancel)

    assert turn.stop_reason == "cancelled"


def test_tool_loop_has_a_ceiling(config, memory):
    agent = make_agent(config, memory, [calls_tool("echo", {"value": "x"})] * 20)

    turn = agent.reply("loop forever")

    assert "stuck in a loop" in turn.error


def test_backend_failures_become_something_sayable(config, memory):
    agent = make_agent(config, memory, [])
    agent.backend.raise_once = BackendError(
        "Ollama is not running at http://localhost:11434. Start it with: ollama serve"
    )

    turn = agent.reply("hello")

    assert "ollama serve" in turn.error
    assert not turn.ok


class TestPrompt:
    def test_the_system_message_carries_persona_and_context(self, config, memory):
        agent = make_agent(config, memory, [says("ok")])

        agent.reply("hello")
        system = agent.backend.calls[0]["messages"][0]

        assert system["role"] == "system"
        assert "JARVIS" in system["content"]
        assert "Current context" in system["content"]

    def test_memories_reach_the_prompt(self, config, memory):
        memory.add("The user prefers metric units.")
        agent = make_agent(config, memory, [says("Noted.")])

        agent.reply("how far is it")

        assert "metric units" in agent.backend.calls[0]["messages"][0]["content"]

    def test_tools_are_sent_in_function_calling_shape(self, config, memory):
        agent = make_agent(config, memory, [says("ok")])

        agent.reply("hello")
        tool = agent.backend.calls[0]["tools"][0]

        assert tool["type"] == "function"
        assert tool["function"]["name"] == "echo"
        assert tool["function"]["parameters"]["required"] == ["value"]


class TestHistoryTrimming:
    def test_short_conversations_are_sent_whole(self, config, memory):
        agent = make_agent(config, memory, [says("ok")])

        agent.reply("hello")

        assert len(agent.backend.calls[0]["messages"]) == 2  # system + user

    def test_old_exchanges_are_dropped_when_context_runs_out(self, config, memory):
        config.context_tokens = 1200  # Small window, to force trimming.
        agent = make_agent(config, memory, [says("ok")])
        for index in range(40):
            agent.messages.append({"role": "user", "content": f"question {index} " * 20})
            agent.messages.append({"role": "assistant", "content": "answer " * 20})

        agent.reply("the latest question")
        sent = agent.backend.calls[0]["messages"]

        assert sent[-1]["content"] == "the latest question"
        assert len(sent) < len(agent.messages)

    def test_trimming_never_orphans_a_tool_result(self, config, memory):
        config.context_tokens = 1200
        agent = make_agent(config, memory, [says("ok")])
        for index in range(30):
            agent.messages.append({"role": "user", "content": f"q{index} " * 20})
            agent.messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "t", "name": "echo", "arguments": {}}],
                }
            )
            agent.messages.append(
                {"role": "tool", "tool_call_id": "t", "name": "echo", "content": "x" * 80}
            )
            agent.messages.append({"role": "assistant", "content": "done " * 20})

        agent.reply("latest")
        sent = agent.backend.calls[0]["messages"][1:]  # skip the system message

        # The first surviving message must not be a dangling tool result.
        assert sent[0]["role"] != "tool"


def test_reset_clears_the_conversation(config, memory):
    agent = make_agent(config, memory, [says("ok")])
    agent.reply("hello")

    agent.reset()

    assert agent.messages == []


def test_transcript_records_the_backend_used(config, memory):
    agent = make_agent(config, memory, [says("Saved.")])
    agent.reply("remember this")

    path = agent.save_transcript()

    assert "fake-model" in path.read_text()
    assert "remember this" in path.read_text()
