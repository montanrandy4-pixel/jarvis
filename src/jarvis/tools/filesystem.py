"""File tools, confined to the configured workspace roots."""

from __future__ import annotations

from pathlib import Path

from . import Tool, ToolResult

MAX_READ_BYTES = 200_000


class OutsideWorkspace(Exception):
    pass


def _resolve(config, raw: str) -> Path:
    """Resolve a path and refuse anything outside the workspace roots."""
    path = Path(raw).expanduser()
    # Resolve before comparing so `~/notes/../../etc/passwd` cannot escape.
    resolved = path.resolve()
    roots = config.workspace_roots()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    allowed = ", ".join(str(r) for r in roots)
    raise OutsideWorkspace(
        f"{resolved} is outside the permitted workspace ({allowed})."
    )


def tools(config) -> list[Tool]:
    def read_file(path: str, max_bytes: int = MAX_READ_BYTES) -> ToolResult:
        try:
            target = _resolve(config, path)
        except OutsideWorkspace as exc:
            return ToolResult(str(exc), is_error=True)
        if not target.is_file():
            return ToolResult(f"No file at {target}.", is_error=True)
        limit = max(1, min(int(max_bytes), MAX_READ_BYTES))
        data = target.read_bytes()[:limit]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                f"{target} is not UTF-8 text ({len(data)} bytes read).",
                is_error=True,
            )
        suffix = "\n... (truncated)" if target.stat().st_size > limit else ""
        return ToolResult(text + suffix, display=f"read {target}")

    def write_file(path: str, content: str) -> ToolResult:
        try:
            target = _resolve(config, path)
        except OutsideWorkspace as exc:
            return ToolResult(str(exc), is_error=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.is_file()
        target.write_text(content)
        verb = "Overwrote" if existed else "Wrote"
        return ToolResult(
            f"{verb} {target} ({len(content)} characters).",
            display=f"wrote {target}",
        )

    def list_directory(path: str = ".") -> ToolResult:
        try:
            target = _resolve(config, path)
        except OutsideWorkspace as exc:
            return ToolResult(str(exc), is_error=True)
        if not target.is_dir():
            return ToolResult(f"No directory at {target}.", is_error=True)
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not entries:
            return ToolResult(f"{target} is empty.")
        lines = []
        for entry in entries[:200]:
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{entry.name} ({size} bytes)")
        more = f"\n... and {len(entries) - 200} more" if len(entries) > 200 else ""
        return ToolResult(
            f"{target}:\n" + "\n".join(lines) + more,
            display=f"listed {target}",
        )

    return [
        Tool(
            name="read_file",
            description=(
                "Read a UTF-8 text file from the user's machine. Use this "
                "whenever the user asks what a file says or contains."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "max_bytes": {
                        "type": "integer",
                        "description": f"Bytes to read (max {MAX_READ_BYTES}).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_file,
            summarize=lambda a: f"read {a.get('path')}",
        ),
        Tool(
            name="write_file",
            description=(
                "Create a file or replace its contents. The whole file is "
                "overwritten, so include everything it should end up containing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to write."},
                    "content": {"type": "string", "description": "Full contents."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=write_file,
            mutates=True,
            summarize=lambda a: (
                f"write {len(a.get('content', ''))} characters to {a.get('path')}"
            ),
        ),
        Tool(
            name="list_directory",
            description="List the files and folders in a directory.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list."}
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=list_directory,
            summarize=lambda a: f"list {a.get('path', '.')}",
        ),
    ]
