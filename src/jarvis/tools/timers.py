"""Timers and reminders -- the thing every voice assistant is asked for first."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from . import Tool, ToolResult


@dataclass
class Timer:
    id: int
    label: str
    fires_at: float
    handle: threading.Timer

    def remaining(self) -> float:
        return max(0.0, self.fires_at - time.time())


@dataclass
class TimerService:
    """Keeps track of pending timers and calls back when one fires."""

    on_fire: object = None  # Callable[[Timer], None], set by the front end.
    _timers: dict[int, Timer] = field(default_factory=dict)
    _next_id: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, seconds: float, label: str) -> Timer:
        with self._lock:
            timer_id = self._next_id
            self._next_id += 1

        def fire() -> None:
            with self._lock:
                self._timers.pop(timer_id, None)
            if callable(self.on_fire):
                self.on_fire(timer)

        handle = threading.Timer(seconds, fire)
        handle.daemon = True
        timer = Timer(
            id=timer_id,
            label=label,
            fires_at=time.time() + seconds,
            handle=handle,
        )
        with self._lock:
            self._timers[timer_id] = timer
        handle.start()
        return timer

    def all(self) -> list[Timer]:
        with self._lock:
            return sorted(self._timers.values(), key=lambda t: t.fires_at)

    def cancel(self, timer_id: int) -> bool:
        with self._lock:
            timer = self._timers.pop(timer_id, None)
        if timer is None:
            return False
        timer.handle.cancel()
        return True

    def cancel_all(self) -> None:
        for timer in self.all():
            self.cancel(timer.id)


def human_duration(seconds: float) -> str:
    """Render a duration the way it would be said out loud."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    parts = []
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs and not hours:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " and ".join(parts)


SERVICE = TimerService()


def tools(service: TimerService | None = None) -> list[Tool]:
    service = service or SERVICE

    def set_timer(seconds: float, label: str = "") -> ToolResult:
        if seconds <= 0:
            return ToolResult("A timer needs a positive duration.", is_error=True)
        if seconds > 86_400:
            return ToolResult("Timers are limited to 24 hours.", is_error=True)
        timer = service.add(float(seconds), label)
        spoken = human_duration(seconds)
        name = f" for {label}" if label else ""
        return ToolResult(
            f"Timer {timer.id} set{name}, going off in {spoken}.",
            display=f"timer {timer.id}: {spoken}",
        )

    def list_timers() -> ToolResult:
        pending = service.all()
        if not pending:
            return ToolResult("No timers are running.")
        lines = [
            f"[{t.id}] {t.label or 'timer'} -- {human_duration(t.remaining())} left"
            for t in pending
        ]
        return ToolResult("\n".join(lines))

    def cancel_timer(timer_id: int) -> ToolResult:
        if service.cancel(int(timer_id)):
            return ToolResult(f"Cancelled timer {timer_id}.", display="timer cancelled")
        return ToolResult(f"No timer with id {timer_id}.", is_error=True)

    return [
        Tool(
            name="set_timer",
            description=(
                "Set a countdown timer. JARVIS announces it out loud when it "
                "fires. Convert what the user said into seconds yourself."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "Duration."},
                    "label": {
                        "type": "string",
                        "description": "What it is for, e.g. 'the pasta'.",
                    },
                },
                "required": ["seconds"],
                "additionalProperties": False,
            },
            handler=set_timer,
        ),
        Tool(
            name="list_timers",
            description="List the timers currently running and their time left.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=list_timers,
        ),
        Tool(
            name="cancel_timer",
            description="Cancel a running timer by its id.",
            input_schema={
                "type": "object",
                "properties": {
                    "timer_id": {"type": "integer", "description": "Timer id."}
                },
                "required": ["timer_id"],
                "additionalProperties": False,
            },
            handler=cancel_timer,
        ),
    ]
