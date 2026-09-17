"""Tools that let JARVIS keep and retrieve facts between sessions."""

from __future__ import annotations

from . import Tool, ToolResult


def tools(memory) -> list[Tool]:
    def remember(text: str, tags: list | None = None) -> ToolResult:
        clean_tags = [str(t) for t in (tags or [])]
        item = memory.add(text, clean_tags)
        return ToolResult(f"Saved as memory {item.id}.", display=f"remembered: {text}")

    def recall(query: str = "") -> ToolResult:
        hits = memory.search(query)
        if not hits:
            return ToolResult("Nothing saved that matches.")
        return ToolResult("\n".join(m.describe() for m in hits[:25]))

    def forget(memory_id: str) -> ToolResult:
        if memory.forget(memory_id):
            return ToolResult(f"Deleted memory {memory_id}.", display="forgot an item")
        return ToolResult(f"No memory with id {memory_id}.", is_error=True)

    return [
        Tool(
            name="remember",
            description=(
                "Save a durable fact about the user or their setup so it is "
                "available in future conversations -- preferences, names, "
                "recurring details, standing instructions. Save these as you "
                "learn them, without being asked. Do not save one-off chatter."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "The fact, written as a standalone sentence that "
                            "will still make sense months from now."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional keywords for later retrieval.",
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=remember,
            summarize=lambda a: f"remember: {a.get('text', '')}",
        ),
        Tool(
            name="recall",
            description=(
                "Search saved memories. Saved facts are already in your context "
                "each turn, so only use this to search a large memory or to get "
                "an id before forgetting something."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms."}
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=recall,
        ),
        Tool(
            name="forget",
            description="Delete a saved memory by its id, when asked to.",
            input_schema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "Id shown in brackets by recall.",
                    }
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            handler=forget,
            mutates=True,
            summarize=lambda a: f"forget memory {a.get('memory_id')}",
        ),
    ]
