"""Telling you something is wrong, without becoming noise.

An alerting system earns its keep by what it *doesn't* send. A watcher that
reports the same broken product every ten minutes trains you to ignore it,
and then the one alert that mattered arrives into a muted channel.

So three rules are built in:

* **Deduplicate.** An alert has a fingerprint. The same fingerprint does not
  fire again until its cooldown expires, however many passes see it.
* **Say when it clears.** A problem that fixes itself sends a short "resolved"
  so you are not left wondering.
* **Route by severity.** A blocker can wake you up; a note goes in the log and
  waits for you to come looking.

Channels are best-effort and independent: a broken webhook must never stop a
desktop notification, and no channel failure may stop the watch loop.
"""

from __future__ import annotations

import json
import logging
import shutil
import smtplib
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("storefront.alerts")

CRITICAL = "critical"   # money is being lost right now
WARNING = "warning"     # will cost you if ignored
INFO = "info"           # worth knowing, not worth interrupting

RANK = {CRITICAL: 0, WARNING: 1, INFO: 2}


@dataclass
class Alert:
    severity: str
    title: str
    detail: str = ""
    # Stable identity for deduplication: the same problem, the same key.
    key: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = f"{self.severity}:{self.title}"

    def line(self) -> str:
        mark = {CRITICAL: "!!", WARNING: " !", INFO: "  "}[self.severity]
        return f"{mark} {self.title}" + (f" -- {self.detail}" if self.detail else "")


@dataclass
class AlertState:
    """What has already been said, so it is not said again."""

    path: Path
    fired: dict[str, str] = field(default_factory=dict)  # key -> ISO timestamp

    @classmethod
    def load(cls, path: Path) -> "AlertState":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(path=path)
        return cls(path=path, fired=dict(raw.get("fired") or {}))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fired": self.fired}, indent=2) + "\n", "utf-8")
        tmp.replace(self.path)

    def due(self, alert: Alert, cooldown_minutes: int) -> bool:
        last = self.fired.get(alert.key)
        if not last:
            return True
        try:
            when = datetime.fromisoformat(last)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - when >= timedelta(minutes=cooldown_minutes)

    def mark(self, alert: Alert) -> None:
        self.fired[alert.key] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def clear(self, keys: set[str]) -> list[str]:
        """Forget keys that no longer apply; return the ones that had fired."""
        resolved = [k for k in list(self.fired) if k not in keys]
        for key in resolved:
            self.fired.pop(key, None)
        return resolved


# --------------------------------------------------------------------------
# Channels. Each returns True on success, never raises.
# --------------------------------------------------------------------------

def to_console(alerts: list[Alert], stream=sys.stdout) -> bool:
    for alert in alerts:
        print(alert.line(), file=stream)
    stream.flush()
    return True


def to_desktop(alerts: list[Alert]) -> bool:
    """A native notification, on whichever desktop this is."""
    if not alerts:
        return True
    worst = min(alerts, key=lambda a: RANK[a.severity])
    title = f"Shop: {worst.title}"[:120]
    body = (f"{len(alerts)} alert(s)" if len(alerts) > 1 else worst.detail)[:220]
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            subprocess.run(
                ["osascript", "-e",
                 f'display notification {json.dumps(body)} with title {json.dumps(title)}'],
                check=False, capture_output=True, timeout=10,
            )
            return True
        if shutil.which("notify-send"):
            urgency = "critical" if worst.severity == CRITICAL else "normal"
            subprocess.run(["notify-send", "-u", urgency, title, body],
                           check=False, capture_output=True, timeout=10)
            return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("desktop notification failed: %s", exc)
    return False


def to_webhook(alerts: list[Alert], url: str, *, timeout: float = 10.0) -> bool:
    """Slack, Discord and most chat tools accept a JSON body with `text`."""
    if not url or not alerts:
        return bool(url)
    text = "\n".join(a.line() for a in alerts)
    payload = json.dumps({"text": f"*Shop alerts*\n{text}"}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("webhook failed: %s", exc)
        return False


def to_email(alerts: list[Alert], settings: dict) -> bool:
    """SMTP. Configure it or leave it off; there is no middle setting."""
    if not alerts or not settings.get("to"):
        return True
    host = settings.get("host", "")
    if not host:
        return False
    message = EmailMessage()
    worst = min(alerts, key=lambda a: RANK[a.severity])
    message["Subject"] = f"[shop] {worst.title}"[:150]
    message["From"] = settings.get("from") or settings["to"]
    message["To"] = settings["to"]
    message.set_content("\n".join(a.line() for a in alerts))
    try:
        port = int(settings.get("port", 587))
        with smtplib.SMTP(host, port, timeout=20) as server:
            if settings.get("starttls", True):
                server.starttls()
            if settings.get("username"):
                server.login(settings["username"], settings.get("password", ""))
            server.send_message(message)
        return True
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        log.warning("email failed: %s", exc)
        return False


def to_file(alerts: list[Alert], path: Path) -> bool:
    if not alerts:
        return True
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for alert in alerts:
                handle.write(f"{stamp} {alert.line()}\n")
        return True
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)
        return False


@dataclass
class Dispatcher:
    """Where alerts go, and how loud each one has to be to get there."""

    state: AlertState
    cooldown_minutes: int = 60
    console: bool = True
    desktop: bool = False
    webhook_url: str = ""
    email: dict = field(default_factory=dict)
    log_path: Path | None = None
    # Minimum severity per channel: interrupt for blockers, log everything.
    desktop_min: str = WARNING
    webhook_min: str = WARNING
    email_min: str = CRITICAL

    def dispatch(self, alerts: list[Alert]) -> tuple[list[Alert], list[str]]:
        """Send what is due. Returns (sent, resolved_keys)."""
        current = {a.key for a in alerts}
        resolved = self.state.clear(current)

        due = [a for a in alerts if self.state.due(a, self.cooldown_minutes)]
        ordered = sorted(due, key=lambda a: RANK[a.severity])

        if self.console:
            to_console(ordered)
            for key in resolved:
                print(f"   resolved: {key}")
        if self.log_path:
            to_file(ordered, self.log_path)
        if ordered:
            if self.desktop:
                to_desktop(self._at_least(ordered, self.desktop_min))
            if self.webhook_url:
                to_webhook(self._at_least(ordered, self.webhook_min), self.webhook_url)
            if self.email.get("to"):
                to_email(self._at_least(ordered, self.email_min), self.email)

        for alert in ordered:
            self.state.mark(alert)
        self.state.save()
        return ordered, resolved

    @staticmethod
    def _at_least(alerts: list[Alert], severity: str) -> list[Alert]:
        return [a for a in alerts if RANK[a.severity] <= RANK[severity]]
