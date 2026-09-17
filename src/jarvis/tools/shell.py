"""Shell access, gated by the `shell` config setting.

`confirm` (the default) runs read-only commands freely and asks before anything
that looks like it writes. The classifier is a heuristic and deliberately
errs toward asking -- an unrecognised command counts as a write.
"""

from __future__ import annotations

import shlex
import subprocess

from . import Tool, ToolResult

# Commands that only report state. Everything else needs confirmation.
READ_ONLY = {
    "arch", "cal", "cat", "date", "df", "diff", "dirname", "du", "echo", "env",
    "file", "find", "free", "grep", "head", "history", "hostname", "id",
    "ifconfig", "ip", "jobs", "less", "ls", "lsof", "man", "md5sum", "nproc",
    "ping", "printenv", "ps", "pwd", "readlink", "realpath", "sort", "stat",
    "tail", "top", "tree", "uname", "uptime", "uniq", "wc", "which", "who",
    "whoami",
}
# Git and friends are read-only only in their reporting subcommands.
SUBCOMMAND_SAFE = {
    "git": {"status", "log", "diff", "show", "branch", "remote", "config"},
    "docker": {"ps", "images", "logs", "inspect"},
    "systemctl": {"status", "list-units", "show"},
    "brew": {"list", "info", "outdated"},
    "pip": {"list", "show", "freeze"},
    "pip3": {"list", "show", "freeze"},
}


def is_read_only(command: str) -> bool:
    """Best-effort guess at whether a command only observes the system."""
    # Anything that chains, redirects or substitutes gets the careful path.
    if any(token in command for token in ("|", ">", "<", "&", ";", "$(", "`")):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    name = parts[0].rsplit("/", 1)[-1]
    if name in SUBCOMMAND_SAFE:
        args = [p for p in parts[1:] if not p.startswith("-")]
        return bool(args) and args[0] in SUBCOMMAND_SAFE[name]
    return name in READ_ONLY


def tools(config) -> list[Tool]:
    always_confirm = config.shell == "confirm"

    def run_shell(command: str, purpose: str = "") -> ToolResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=config.shell_timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                f"Command timed out after {config.shell_timeout:g} seconds.",
                is_error=True,
            )
        output = (proc.stdout or "") + (
            f"\n[stderr]\n{proc.stderr}" if proc.stderr.strip() else ""
        )
        output = output.strip() or "(no output)"
        if len(output) > 20_000:
            output = output[:20_000] + "\n... (truncated)"
        if proc.returncode != 0:
            return ToolResult(
                f"Exit code {proc.returncode}.\n{output}",
                is_error=True,
                display=f"$ {command} (exit {proc.returncode})",
            )
        return ToolResult(output, display=f"$ {command}")

    return [
        Tool(
            name="run_shell",
            description=(
                "Run a shell command on the user's machine and get its output. "
                "Use it to inspect or control the system: check what is "
                "running, look at disk space, open an application, and so on. "
                "Prefer a single command that answers the question outright."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command line to run.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": (
                            "Short plain-language reason, read to the user when "
                            "confirmation is needed, e.g. 'check free disk "
                            "space'."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=run_shell,
            # Under `confirm`, only commands that might change something
            # interrupt the user. Under `on`, nothing does.
            mutates=(
                (lambda args: not is_read_only(args.get("command", "")))
                if always_confirm
                else False
            ),
            summarize=lambda a: (
                f"run `{a.get('command', '')}`"
                + (f" to {a['purpose']}" if a.get("purpose") else "")
            ),
        )
    ]
