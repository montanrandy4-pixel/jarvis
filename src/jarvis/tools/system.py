"""A quick read of the machine's vital signs, for 'how are we doing' questions.

:func:`snapshot` returns the numbers (the app's HUD polls it); the
``system_status`` tool turns the same numbers into sentences for the model.
Every probe degrades to ``None`` rather than raising, because a missing
battery or an unfamiliar OS is not an error.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time

from . import Tool, ToolResult


def _run(*argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout


# --- probes -----------------------------------------------------------------


def uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            return ctypes.windll.kernel32.GetTickCount64() / 1000.0
        except Exception:
            return None
    try:  # macOS and the BSDs.
        boot = int(_run("sysctl", "-n", "kern.boottime").split("sec = ")[1].split(",")[0])
        return time.time() - boot
    except Exception:
        return None


def memory_gb() -> tuple[float, float] | None:
    """(available, total) in GB."""
    try:
        info = {}
        with open("/proc/meminfo") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                info[key] = int(value.split()[0])
        total = info["MemTotal"] / 1048576
        available = info.get("MemAvailable", info.get("MemFree", 0)) / 1048576
        return available, total
    except (OSError, KeyError, ValueError, IndexError):
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullAvailPhys / 2**30, status.ullTotalPhys / 2**30
        except Exception:
            return None
    try:  # macOS: free + inactive + speculative pages are available.
        total = int(_run("sysctl", "-n", "hw.memsize").strip())
        stats = _run("vm_stat")
        page = int(stats.split("page size of ")[1].split()[0])
        pages = {}
        for line in stats.splitlines()[1:]:
            key, _, value = line.partition(":")
            pages[key.strip()] = int(value.strip().rstrip(".") or 0)
        free = sum(
            pages.get(k, 0)
            for k in ("Pages free", "Pages inactive", "Pages speculative")
        )
        return free * page / 2**30, total / 2**30
    except Exception:
        return None


def battery() -> tuple[int, str] | None:
    """(percent, status) where status is charging, discharging, full or unknown."""
    base = "/sys/class/power_supply"
    try:
        for entry in sorted(os.listdir(base)):
            if not entry.startswith("BAT"):
                continue
            with open(f"{base}/{entry}/capacity") as handle:
                percent = int(handle.read().strip())
            try:
                with open(f"{base}/{entry}/status") as handle:
                    status = handle.read().strip().lower()
            except OSError:
                status = "unknown"
            return percent, status
    except (OSError, ValueError):
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class PowerStatus(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_ubyte),
                    ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            status = PowerStatus()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                return None
            # 128 = no system battery, 255 = unknown.
            if status.BatteryFlag & 128 or status.BatteryLifePercent == 255:
                return None
            state = "charging" if status.ACLineStatus == 1 else "discharging"
            return int(status.BatteryLifePercent), state
        except Exception:
            return None
    try:  # macOS.
        out = _run("pmset", "-g", "batt")
        for line in out.splitlines():
            if "%" not in line:
                continue
            parts = [p.strip() for p in line.split("\t", 1)[-1].split(";")]
            percent = int(parts[0].rstrip("%"))
            status = parts[1] if len(parts) > 1 else "unknown"
            if status in {"charged", "finishing charge"}:
                status = "full"
            return percent, status
    except Exception:
        pass
    return None


_last_cpu_times: tuple[int, int] | None = None


def cpu_percent() -> float | None:
    """Rough CPU use, 0-100.

    Load average over core count on Unix; on Windows the busy share of CPU time
    since the previous call (the HUD polls every few seconds, so this is a
    rolling reading).
    """
    if sys.platform == "win32":
        global _last_cpu_times
        try:
            import ctypes
            from ctypes import wintypes

            idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
            ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )

            def ticks(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            # Kernel time includes idle time.
            now = (ticks(idle), ticks(kernel) + ticks(user))
            previous, _last_cpu_times = _last_cpu_times, now
            if previous is None:
                return None
            idle_delta = now[0] - previous[0]
            total_delta = now[1] - previous[1]
            if total_delta <= 0:
                return None
            return max(0.0, min(100.0, 100.0 * (1 - idle_delta / total_delta)))
        except Exception:
            return None
    try:
        load = os.getloadavg()[0]
    except (OSError, AttributeError):
        return None
    return max(0.0, min(100.0, 100.0 * load / (os.cpu_count() or 1)))


def snapshot() -> dict:
    """The machine's vital signs as numbers. Missing readings are ``None``."""
    memory = memory_gb()
    power = battery()
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        disk = {
            "free_gb": round(usage.free / 2**30, 1),
            "total_gb": round(usage.total / 2**30, 1),
            "used_pct": round(100 * (1 - usage.free / usage.total), 1),
        }
    except (OSError, ZeroDivisionError):
        disk = None
    return {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpus": os.cpu_count(),
        "cpu_pct": _round(cpu_percent()),
        "uptime_s": _round(uptime_seconds(), 0),
        "memory": None
        if memory is None
        else {
            "available_gb": round(memory[0], 1),
            "total_gb": round(memory[1], 1),
            "used_pct": round(100 * (1 - memory[0] / memory[1]), 1)
            if memory[1]
            else None,
        },
        "disk": disk,
        "battery": None if power is None else {"pct": power[0], "status": power[1]},
    }


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


# --- the tool ---------------------------------------------------------------


def _describe_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    days, rest = divmod(int(seconds), 86_400)
    hours, minutes = divmod(rest // 60, 60)
    if days:
        return f"{days} days, {hours} hours"
    return f"{hours} hours, {minutes} minutes"


def describe(snap: dict) -> str:
    """The snapshot as the lines the model reads."""
    try:
        load = ", ".join(f"{v:.2f}" for v in os.getloadavg())
        load_line = f"Load average: {load} across {snap['cpus']} CPUs"
    except (OSError, AttributeError):
        cpu = snap["cpu_pct"]
        load_line = f"CPU: {'unknown' if cpu is None else f'{cpu:.0f}% busy'}"
    memory = snap["memory"]
    disk = snap["disk"]
    power = snap["battery"]
    return "\n".join(
        [
            f"Host: {snap['host']} -- {snap['os']}",
            f"Uptime: {_describe_uptime(snap['uptime_s'])}",
            load_line,
            "Memory: unknown"
            if memory is None
            else f"Memory: {memory['available_gb']:.1f} GB free of "
            f"{memory['total_gb']:.1f} GB ({memory['used_pct']:.0f}% in use)",
            "Disk (home): unknown"
            if disk is None
            else f"Disk (home): {disk['free_gb']:.1f} GB free of "
            f"{disk['total_gb']:.1f} GB",
            "Battery: no battery detected"
            if power is None
            else f"Battery: {power['pct']}% ({power['status']})",
        ]
    )


def tools(config) -> list[Tool]:
    def system_status() -> ToolResult:
        return ToolResult(describe(snapshot()), display="checked system status")

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
