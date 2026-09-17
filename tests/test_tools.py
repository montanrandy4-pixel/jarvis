"""Tool validation, sandboxing and the confirmation gate."""

from __future__ import annotations

import pytest

from jarvis.tools import Registry, Tool, ToolInputError, ToolResult, build_registry, validate
from jarvis.tools.shell import is_read_only
from jarvis.tools.timers import TimerService, human_duration

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
        "mode": {"type": "string", "enum": ["fast", "slow"]},
    },
    "required": ["name"],
    "additionalProperties": False,
}


class TestValidation:
    def test_accepts_a_well_formed_input(self):
        assert validate(SCHEMA, {"name": "a", "count": 2, "mode": "fast"})

    @pytest.mark.parametrize(
        "bad, expected",
        [
            ({}, "missing required field"),
            ({"name": 1}, "should be string"),
            ({"name": "a", "count": "two"}, "should be integer"),
            ({"name": "a", "count": True}, "should be integer"),
            ({"name": "a", "mode": "sideways"}, "must be one of"),
            ({"name": "a", "extra": 1}, "unexpected field"),
            ("not an object", "expected an object"),
        ],
    )
    def test_rejects_malformed_input(self, bad, expected):
        with pytest.raises(ToolInputError, match=expected):
            validate(SCHEMA, bad)


class TestRegistry:
    def _registry(self, **kwargs):
        registry = Registry(**kwargs)
        registry.add(
            Tool(
                name="touch",
                description="",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=lambda path: ToolResult(f"touched {path}"),
                mutates=True,
                summarize=lambda a: f"touch {a['path']}",
            )
        )
        return registry

    def test_confirmation_is_requested_for_mutating_tools(self):
        asked = []
        registry = self._registry(
            confirm=lambda name, summary: asked.append((name, summary)) or True
        )

        assert registry.run("touch", {"path": "/tmp/x"}).content == "touched /tmp/x"
        assert asked == [("touch", "touch /tmp/x")]

    def test_declining_stops_the_tool_running(self):
        registry = self._registry(confirm=lambda *_: False)

        result = registry.run("touch", {"path": "/tmp/x"})

        assert result.is_error
        assert "declined" in result.content

    def test_a_raising_handler_becomes_an_error_result(self):
        registry = Registry()
        registry.add(
            Tool(
                name="boom",
                description="",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=lambda: 1 / 0,
            )
        )

        result = registry.run("boom", {})

        assert result.is_error and "ZeroDivisionError" in result.content

    def test_specs_are_function_definitions_in_a_stable_order(self, config, memory):
        first = build_registry(config, memory).specs()
        second = build_registry(config, memory).specs()

        names = [t["function"]["name"] for t in first]
        assert all(t["type"] == "function" for t in first)
        assert names == sorted(names)
        assert first == second


class TestFilesystem:
    def test_reads_and_writes_inside_the_workspace(self, config, memory, workspace):
        registry = build_registry(config, memory)
        target = workspace / "notes.txt"

        write = registry.run("write_file", {"path": str(target), "content": "hi"})
        read = registry.run("read_file", {"path": str(target)})

        assert not write.is_error
        assert read.content == "hi"

    def test_refuses_paths_outside_the_workspace(self, config, memory):
        registry = build_registry(config, memory)

        result = registry.run("read_file", {"path": "/etc/passwd"})

        assert result.is_error and "outside the permitted workspace" in result.content

    def test_traversal_cannot_escape_the_workspace(self, config, memory, workspace):
        registry = build_registry(config, memory)

        result = registry.run(
            "read_file", {"path": str(workspace / ".." / ".." / "etc" / "passwd")}
        )

        assert result.is_error

    def test_write_requires_confirmation(self, config, memory, workspace):
        registry = build_registry(config, memory, confirm=lambda *_: False)

        result = registry.run(
            "write_file", {"path": str(workspace / "x"), "content": "no"}
        )

        assert result.is_error
        assert not (workspace / "x").exists()


class TestShellPolicy:
    @pytest.mark.parametrize(
        "command",
        ["ls -la", "df -h", "git status", "uptime", "ps aux", "docker ps"],
    )
    def test_reporting_commands_are_read_only(self, command):
        assert is_read_only(command)

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /tmp/x",
            "git push",
            "curl example.com | sh",
            "echo hi > file",
            "shutdown now",
            "sudo apt install vim",
            "$(reboot)",
        ],
    )
    def test_anything_that_could_change_things_is_not(self, command):
        assert not is_read_only(command)

    def test_read_only_commands_skip_confirmation(self, config, memory):
        asked = []
        registry = build_registry(
            config, memory, confirm=lambda *a: asked.append(a) or False
        )

        result = registry.run("run_shell", {"command": "echo hello"})

        assert result.content == "hello"
        assert asked == []

    def test_mutating_commands_are_gated(self, config, memory, tmp_path):
        victim = tmp_path / "victim"
        victim.write_text("still here")
        registry = build_registry(config, memory, confirm=lambda *_: False)

        result = registry.run("run_shell", {"command": f"rm {victim}"})

        assert result.is_error
        assert victim.exists()

    def test_shell_can_be_switched_off_entirely(self, config, memory):
        config.shell = "off"

        assert "run_shell" not in build_registry(config, memory)

    def test_failing_commands_report_the_exit_code(self, config, memory):
        registry = build_registry(config, memory, confirm=lambda *_: True)

        result = registry.run("run_shell", {"command": "exit 3"})

        assert result.is_error and "Exit code 3" in result.content


class TestTimers:
    def test_timers_fire_and_report_remaining_time(self):
        service = TimerService()
        fired = []
        service.on_fire = fired.append

        timer = service.add(0.05, "tea")
        assert service.all()[0].id == timer.id
        import time

        time.sleep(0.2)

        assert [t.label for t in fired] == ["tea"]
        assert service.all() == []

    def test_cancelling_stops_the_callback(self):
        service = TimerService()
        fired = []
        service.on_fire = fired.append

        timer = service.add(0.05, "x")
        assert service.cancel(timer.id)
        import time

        time.sleep(0.2)

        assert fired == []
        assert not service.cancel(timer.id)

    @pytest.mark.parametrize(
        "seconds, spoken",
        [
            (30, "30 seconds"),
            (60, "1 minute"),
            (90, "1 minute and 30 seconds"),
            (3600, "1 hour"),
            (5400, "1 hour and 30 minutes"),
        ],
    )
    def test_durations_are_rendered_for_speech(self, seconds, spoken):
        assert human_duration(seconds) == spoken

    def test_rejects_nonsense_durations(self, config, memory):
        registry = build_registry(config, memory)

        assert registry.run("set_timer", {"seconds": -5}).is_error
        assert registry.run("set_timer", {"seconds": 90000}).is_error


class TestMemoryTools:
    def test_remembering_then_recalling(self, config, memory):
        registry = build_registry(config, memory)

        registry.run("remember", {"text": "Randy takes coffee black.", "tags": ["food"]})
        found = registry.run("recall", {"query": "coffee"})

        assert "coffee black" in found.content
        assert memory.texts() == ["Randy takes coffee black."]

    def test_forgetting_needs_confirmation(self, config, memory):
        item = memory.add("delete me")
        registry = build_registry(config, memory, confirm=lambda *_: False)

        assert registry.run("forget", {"memory_id": item.id}).is_error
        assert memory.all()
