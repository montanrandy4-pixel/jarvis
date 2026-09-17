"""A quick read of the machine's vital signs, for 'how are we doing' questions."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time

from . import Tool, ToolResult


def _uptime() -> str:
    try:
        with open("/proc/uptime") as handle:
            seconds = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        try:  # macOS and the BSDs.
            out = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            boot = int(out.split("sec = ")[1].split(",")[0])
            seconds = time.time() - boot
        except Exception:
            return "unknown"
    days, rest = divmod(int(seconds), 86_400)
    hours, minutes = divmod(rest // 60, 60)
    if days:
        return f"{days} days, {hours} hours"
    return f"{hours} hours, {minutes} minutes"


def _memory() -> str:
    try:
        info = {}
        with open("/proc/meminfo") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                info[key] = int(value.split()[0])
        total = info["MemTotal"] / 1048576
        available = info.get("MemAvailable", info.get("MemFree", 0)) / 1048576
        used_pct = 100 * (1 - available / total) if total else 0
        return f"{available:.1f} GB free of {total:.1f} GB ({used_pct:.0f}% in use)"
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return "unknown"


def _battery() -> str:
    base = "/sys/class/power_supply"
    try:
        for entry in sorted(os.listdir(base)):
            if not entry.startswith("BAT"):
                continue
            with open(f"{base}/{entry}/capacity") as handle:
                percent = handle.read().strip()
            try:
                with open(f"{base}/{entry}/status") as handle:
                    status = handle.read().strip().lower()
            except OSError:
                status = "unknown"
            return f"{percent}% ({status})"
    except OSError:
        pass
    try:  # macOS.
        out = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5
        ).stdout
        for token in out.split():
            if token.endswith("%;"):
                return token.rstrip(";")
    except Exception:
        pass
    return "no battery detected"


def tools(config) -> list[Tool]:
    def system_status() -> ToolResult:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        try:
            load = ", ".join(f"{v:.2f}" for v in os.getloadavg())
        except (OSError, AttributeError):
            load = "unknown"
        lines = [
            f"Host: {platform.node()} -- {platform.system()} {platform.release()}",
            f"Uptime: {_uptime()}",
            f"Load average: {load} across {os.cpu_count()} CPUs",
            f"Memory: {_memory()}",
            f"Disk (home): {usage.free / 2**30:.1f} GB free of "
            f"{usage.total / 2**30:.1f} GB",
            f"Battery: {_battery()}",
        ]
        return ToolResult("\n".join(lines), display="checked system status")

    return [
        Tool(
            name="system_status",
            description=(
                "Report the machine's uptime, CPU load, memory, free disk and "
                "battery. Use this for 'how is the system doing' questions "
                "rather than running several shell commands."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=system_status,
        )
    ]
