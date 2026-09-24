"""Opening JARVIS: finding a running copy, and giving it a window of its own.

Launching JARVIS a second time (the desktop icon, the hotkey, the dock) should
bring up the copy that is already running rather than fail on a busy port, so
:func:`running_instance` asks the port who is there first.

The app looks and behaves like an app when a Chromium-family browser opens it
with ``--app``: no tabs, no address bar, its own taskbar entry. Every desktop
OS has one of those installed more often than not (Edge ships with Windows);
otherwise the default browser opens it in a tab.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def local_url(host: str, port: int) -> str:
    """The address a browser on this machine should open."""
    if host in {"", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def running_instance(url: str, timeout: float = 1.5) -> bool:
    """Is a JARVIS app already serving at this address?"""
    try:
        with urllib.request.urlopen(url + "api/ping", timeout=timeout) as response:
            return json.loads(response.read() or b"{}").get("app") == "jarvis"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def stop_instance(url: str, timeout: float = 3.0) -> bool:
    """Ask a running app to shut down. True if one was running and agreed."""
    request = urllib.request.Request(
        url + "api/shutdown",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}").get("ok") is True
    except (OSError, ValueError, urllib.error.URLError):
        return False


# --- a window of its own ------------------------------------------------------


def _windows_candidates() -> list[Path]:
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    tails = [
        r"Google\Chrome\Application\chrome.exe",
        r"Microsoft\Edge\Application\msedge.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    # Chrome first wherever it is installed, since someone who installed it
    # probably prefers it; Edge is the one every Windows machine has.
    return [Path(root) / tail for tail in tails for root in roots if root]


_MAC_APPS = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Arc"]
_LINUX_BINARIES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
]


def app_window_command(url: str) -> list[str] | None:
    """The command that opens ``url`` as a standalone app window, if possible."""
    flag = f"--app={url}"
    if sys.platform == "darwin":
        for name in _MAC_APPS:
            for base in (Path("/Applications"), Path.home() / "Applications"):
                bundle = base / f"{name}.app"
                if bundle.is_dir():
                    return ["open", "-na", str(bundle), "--args", flag]
        return None
    if sys.platform == "win32":
        for candidate in _windows_candidates():
            if candidate.is_file():
                return [str(candidate), flag]
        for name in ("chrome", "msedge"):
            found = shutil.which(name)
            if found:
                return [found, flag]
        return None
    for name in _LINUX_BINARIES:
        found = shutil.which(name)
        if found:
            return [found, flag]
    return None


def open_window(url: str, *, app_window: bool = True) -> str:
    """Show the app. Returns how it was opened: "app", "browser" or "none"."""
    command = app_window_command(url) if app_window else None
    if command:
        try:
            _detach(command)
            return "app"
        except OSError:
            pass
    try:
        return "browser" if webbrowser.open(url) else "none"
    except webbrowser.Error:
        return "none"


def _detach(command: list[str]) -> None:
    """Start a program that outlives JARVIS and never writes to its terminal."""
    options: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)
