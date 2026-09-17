"""Tool registry, input validation and the built-in tool set.

Tool inputs stream in eagerly (see ``Agent``), which means the SDK can hand us a
truncated or malformed ``input`` dict rather than raising. Every tool input is
therefore validated against its own schema before the handler ever runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ConfirmFn = Callable[[str, str], bool]


class ToolInputError(ValueError):
    """Raised when Claude's tool input does not match the declared schema."""


@dataclass
class ToolResult:
    """What a handler gives back. ``display`` is what a human sees scrolling by."""

    content: str
    is_error: bool = False
    display: str = ""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., ToolResult | str]
    # Tools that change the world ask for confirmation. Either a flat bool, or a
    # predicate over the call's arguments for tools that only sometimes write
    # (the shell being the obvious one).
    mutates: bool | Callable[[dict], bool] = False
    # A one-line human summary of a pending call, used in confirmation prompts.
    summarize: Callable[[dict], str] | None = None

    def spec(self) -> dict:
        """The tool definition sent to the API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            # Inputs are small, but streaming them costs nothing and gets file
            # contents moving before the model finishes the block.
            "eager_input_streaming": True,
        }

    def requires_confirmation(self, args: dict) -> bool:
        if callable(self.mutates):
            try:
                return bool(self.mutates(args))
            except Exception:
                return True  # If the predicate itself fails, ask.
        return bool(self.mutates)

    def describe_call(self, args: dict) -> str:
        if self.summarize:
            try:
                return self.summarize(args)
            except Exception:
                pass
        return self.name


def validate(schema: dict, value: Any) -> dict:
    """Check a tool input against the subset of JSON Schema the tools use.

    Returns the validated dict. Raises :class:`ToolInputError` on any mismatch --
    including the truncated-JSON case, where required keys are simply absent.
    """
    if not isinstance(value, dict):
        raise ToolInputError(f"expected an object, got {type(value).__name__}")

    props: dict = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in value:
            raise ToolInputError(f"missing required field {key!r}")
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in props:
                raise ToolInputError(f"unexpected field {key!r}")

    checkers: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, item in value.items():
        spec = props.get(key)
        if not spec:
            continue
        expected = spec.get("type")
        checker = checkers.get(expected) if isinstance(expected, str) else None
        if checker and not isinstance(item, checker):
            raise ToolInputError(
                f"field {key!r} should be {expected}, got {type(item).__name__}"
            )
        # bool is an int subclass in Python; do not let True pass as a number.
        if expected in {"integer", "number"} and isinstance(item, bool):
            raise ToolInputError(f"field {key!r} should be {expected}, got boolean")
        if spec.get("enum") and item not in spec["enum"]:
            raise ToolInputError(
                f"field {key!r} must be one of {', '.join(map(str, spec['enum']))}"
            )
    return value


@dataclass
class Registry:
    """The set of tools available this session, plus the confirmation policy."""

    tools: dict[str, Tool] = field(default_factory=dict)
    confirm: ConfirmFn | None = None

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def __contains__(self, name: object) -> bool:
        return name in self.tools

    def __len__(self) -> int:
        return len(self.tools)

    def specs(self) -> list[dict]:
        # Sorted so the tool block is byte-stable and stays cacheable.
        return [self.tools[name].spec() for name in sorted(self.tools)]

    def run(self, name: str, raw_input: Any) -> ToolResult:
        """Validate, gate and execute one tool call. Never raises."""
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(f"No such tool: {name}", is_error=True)
        try:
            args = validate(tool.input_schema, raw_input)
        except ToolInputError as exc:
            # Claude sees this and retries with a corrected call.
            return ToolResult(f"Invalid input for {name}: {exc}", is_error=True)

        if self.confirm is not None and tool.requires_confirmation(args):
            if not self.confirm(tool.name, tool.describe_call(args)):
                return ToolResult(
                    "The user declined this action. Do not retry it; ask what "
                    "they would like instead.",
                    is_error=True,
                )
        try:
            result = tool.handler(**args)
        except Exception as exc:  # A tool bug must not kill the conversation.
            return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(str(result))


def build_registry(config, memory, *, confirm: ConfirmFn | None = None) -> Registry:
    """Assemble the standard tool set for a session."""
    from . import filesystem, memory_tools, shell, system, timers

    registry = Registry(confirm=confirm)
    for tool in [
        *filesystem.tools(config),
        *memory_tools.tools(memory),
        *system.tools(config),
        *timers.tools(),
    ]:
        registry.add(tool)
    if config.shell != "off":
        for tool in shell.tools(config):
            registry.add(tool)
    return registry
