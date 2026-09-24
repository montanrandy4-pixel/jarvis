"""``jarvis setup``: put JARVIS where people look for apps.

Creates a launcher in the place each desktop OS keeps them, so JARVIS opens
like any other app rather than from a terminal:

- Windows: Start menu and desktop shortcuts, with Ctrl+Alt+J as a global
  hotkey, and optionally a Startup-folder entry.
- macOS: ``~/Applications/JARVIS.app``, which Spotlight and Launchpad find,
  and optionally a login LaunchAgent.
- Linux: an application-menu entry and desktop icon, and optionally an XDG
  autostart entry.

Every launcher runs this same Python (``sys.executable -m jarvis``), so it
keeps working however JARVIS was installed and whatever is on PATH. Launching
while JARVIS is already running just brings the window up (see
``launcher.running_instance``); starting at login runs it without a window, so
the first launch of the day is instant.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

WEB = Path(__file__).parent / "web"
HOTKEY = "CTRL+ALT+J"
CALL_HOTKEY = "CTRL+ALT+K"
MAC_AGENT = "local.jarvis.assistant"


@dataclass
class Report:
    """What setup did, for the command to print."""

    created: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def python_command(*, windowless: bool = False) -> list[str]:
    """How a launcher starts JARVIS: this interpreter, by absolute path."""
    exe = Path(sys.executable)
    if windowless and sys.platform == "win32":
        quiet = exe.with_name("pythonw.exe")
        if quiet.is_file():
            exe = quiet  # No console window flashing up behind the app.
    return [str(exe), "-m", "jarvis"]


# --- icon formats -----------------------------------------------------------


def ico_bytes(png: bytes) -> bytes:
    """A Windows .ico holding one 256px PNG (supported since Vista)."""
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def icns_bytes(images: dict[str, bytes]) -> bytes:
    """A macOS .icns from PNGs keyed by type: ic08 = 256px, ic09 = 512px."""
    body = b"".join(
        kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data
        for kind, data in images.items()
    )
    return b"icns" + struct.pack(">I", len(body) + 8) + body


# --- Linux ------------------------------------------------------------------


def _desktop_quote(arg: str) -> str:
    """Quote one argument for a .desktop Exec line (freedesktop spec)."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def desktop_entry(
    command: list[str], *, icon: Path, autostart: bool = False, call: list[str] | None = None
) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=JARVIS",
        "GenericName=AI Assistant",
        "Comment=Talk to your AI assistant",
        "Exec=" + " ".join(_desktop_quote(a) for a in command),
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Utility;",
        "Keywords=assistant;ai;voice;jarvis;",
        "StartupNotify=false",
    ]
    if autostart:
        lines += ["X-GNOME-Autostart-enabled=true", "NoDisplay=true"]
    if call:
        # Right-click the launcher (or its dock icon) for "Start a call".
        lines += [
            "Actions=call;",
            "",
            "[Desktop Action call]",
            "Name=Start a call",
            "Exec=" + " ".join(_desktop_quote(a) for a in call),
        ]
    return "\n".join(lines) + "\n"


def _linux_paths(home: Path, env) -> dict[str, Path]:
    data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return {
        "icon": data / "icons" / "hicolor" / "512x512" / "apps" / "jarvis-assistant.png",
        "menu": data / "applications" / "jarvis.desktop",
        "autostart": config / "autostart" / "jarvis.desktop",
    }


def _linux_desktop_dir(home: Path, run) -> Path | None:
    try:
        out = run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        found = Path(out.stdout.strip())
        if out.returncode == 0 and found.is_dir() and found != home:
            return found
    except (OSError, subprocess.SubprocessError):
        pass
    fallback = home / "Desktop"
    return fallback if fallback.is_dir() else None


def _install_linux(report, *, home, env, run, autostart, desktop) -> None:
    paths = _linux_paths(home, env)
    _write(paths["icon"], (WEB / "icon-512.png").read_bytes(), report)
    entry = desktop_entry(
        python_command() + ["app"], icon=paths["icon"], call=python_command() + ["call"]
    )
    _write(paths["menu"], entry.encode(), report)
    if desktop:
        folder = _linux_desktop_dir(home, run)
        if folder:
            target = folder / "jarvis.desktop"
            _write(target, entry.encode(), report, mode=0o755)
            try:  # GNOME asks before running untrusted desktop files.
                run(["gio", "set", str(target), "metadata::trusted", "true"],
                    capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
    if autostart:
        entry = desktop_entry(
            python_command() + ["app", "--no-browser"], icon=paths["icon"], autostart=True
        )
        _write(paths["autostart"], entry.encode(), report)
    else:  # Setup reflects the latest answer, so a "no" undoes an earlier "yes".
        _unlink(paths["autostart"], report)
    report.notes.append(
        "Find JARVIS in your applications menu. For a hotkey, add a custom "
        "keyboard shortcut in Settings that runs: "
        + " ".join(shlex.quote(a) for a in python_command() + ["app"])
    )


def _remove_linux(report, *, home, env, run) -> None:
    paths = _linux_paths(home, env)
    targets = [paths["menu"], paths["autostart"], paths["icon"]]
    folder = _linux_desktop_dir(home, run)
    if folder:
        targets.append(folder / "jarvis.desktop")
    for path in targets:
        _unlink(path, report)


# --- macOS ------------------------------------------------------------------


def _mac_paths(home: Path) -> dict[str, Path]:
    return {
        "app": home / "Applications" / "JARVIS.app",
        "agent": home / "Library" / "LaunchAgents" / f"{MAC_AGENT}.plist",
        "log": home / "Library" / "Logs" / "JARVIS.log",
    }


def mac_launch_script(command: list[str], *, log: Path, path_env: str) -> str:
    # The app bundle only starts JARVIS and exits: a server that never returns
    # would leave the icon bouncing in the Dock. Finder gives apps a bare PATH,
    # so the one from setup time is kept for the shell tool's sake.
    quoted = " ".join(shlex.quote(a) for a in command)
    return (
        "#!/bin/sh\n"
        f"export PATH={shlex.quote(path_env)}\n"
        f"mkdir -p {shlex.quote(str(log.parent))}\n"
        f"nohup {quoted} >>{shlex.quote(str(log))} 2>&1 &\n"
    )


def _install_mac(report, *, home, env, run, autostart, desktop) -> None:
    paths = _mac_paths(home)
    contents = paths["app"] / "Contents"
    bundle = Report()
    info = {
        "CFBundleName": "JARVIS",
        "CFBundleDisplayName": "JARVIS",
        "CFBundleIdentifier": MAC_AGENT,
        "CFBundleExecutable": "JARVIS",
        "CFBundleIconFile": "JARVIS",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "LSMinimumSystemVersion": "10.13",
        "NSHighResolutionCapable": True,
    }
    _write(contents / "Info.plist", plistlib.dumps(info), bundle)
    script = mac_launch_script(
        python_command() + ["app"], log=paths["log"], path_env=env.get("PATH", "")
    )
    _write(contents / "MacOS" / "JARVIS", script.encode(), bundle, mode=0o755)
    icon = icns_bytes(
        {
            "ic08": (WEB / "icon-256.png").read_bytes(),
            "ic09": (WEB / "icon-512.png").read_bytes(),
        }
    )
    _write(contents / "Resources" / "JARVIS.icns", icon, bundle)
    report.created.append(str(paths["app"]))
    if autostart:
        agent = {
            "Label": MAC_AGENT,
            "ProgramArguments": python_command() + ["app", "--no-browser"],
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": env.get("PATH", "")},
            "StandardOutPath": str(paths["log"]),
            "StandardErrorPath": str(paths["log"]),
        }
        _write(paths["agent"], plistlib.dumps(agent), report)
    else:
        _unlink(paths["agent"], report)
    report.notes.append(
        "Open JARVIS from Spotlight (Cmd+Space, type JARVIS) or Launchpad. "
        "Drag it from ~/Applications to the Dock to keep it there."
    )


def _remove_mac(report, *, home, env, run) -> None:
    paths = _mac_paths(home)
    _unlink(paths["agent"], report)
    if paths["app"].exists():
        import shutil

        shutil.rmtree(paths["app"])
        report.removed.append(str(paths["app"]))


# --- Windows ----------------------------------------------------------------


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def windows_script(
    *, target: str, icon: Path, programs: Path, desktop: bool, autostart: bool
) -> str:
    """PowerShell that writes the .lnk shortcuts (only COM can make them)."""

    def shortcut(folder: str, arguments: str, hotkey: str = "", name: str = "JARVIS") -> str:
        lines = [
            f"$s = $shell.CreateShortcut((Join-Path {folder} '{name}.lnk'))",
            f"$s.TargetPath = {_ps_quote(target)}",
            f"$s.Arguments = {_ps_quote(arguments)}",
            f"$s.IconLocation = {_ps_quote(str(icon) + ',0')}",
            "$s.Description = 'Talk to your AI assistant'",
            "$s.WorkingDirectory = $HOME",
        ]
        if hotkey:
            lines.append(f"$s.Hotkey = {_ps_quote(hotkey)}")
        lines.append("$s.Save()")
        return "\n".join(lines)

    parts = [
        "$ErrorActionPreference = 'Stop'",
        "$shell = New-Object -ComObject WScript.Shell",
        f"$programs = {_ps_quote(str(programs))}",
        # A hotkey only works on a shortcut in the Start menu or on the desktop,
        # and only one shortcut may own it.
        shortcut("$programs", "-m jarvis app", HOTKEY),
        shortcut("$programs", "-m jarvis call", CALL_HOTKEY, name="Call JARVIS"),
    ]
    if desktop:
        parts += [
            "$desk = [Environment]::GetFolderPath('Desktop')",
            shortcut("$desk", "-m jarvis app"),
        ]
    if autostart:
        parts.append(
            shortcut("(Join-Path $programs 'Startup')", "-m jarvis app --no-browser")
        )
    return "\n".join(parts) + "\n"


def _windows_paths(home: Path, env) -> dict[str, Path]:
    appdata = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    programs = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return {
        "programs": programs,
        "menu": programs / "JARVIS.lnk",
        "call": programs / "Call JARVIS.lnk",
        "startup": programs / "Startup" / "JARVIS.lnk",
        "icon": local / "JARVIS" / "jarvis.ico",
    }


def _powershell(script: str, run) -> None:
    result = run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "PowerShell failed").strip())


def _install_windows(report, *, home, env, run, autostart, desktop) -> None:
    paths = _windows_paths(home, env)
    _write(paths["icon"], ico_bytes((WEB / "icon-256.png").read_bytes()), report)
    target = python_command(windowless=True)[0]
    _powershell(
        windows_script(
            target=target,
            icon=paths["icon"],
            programs=paths["programs"],
            desktop=desktop,
            autostart=autostart,
        ),
        run,
    )
    report.created.append(str(paths["menu"]))
    report.created.append(str(paths["call"]))
    if desktop:
        report.created.append("Desktop\\JARVIS.lnk")
    if autostart:
        report.created.append(str(paths["startup"]))
    else:
        _unlink(paths["startup"], report)
    report.notes.append(
        "Press Ctrl+Alt+J anywhere to open JARVIS, or Ctrl+Alt+K to call it "
        "hands-free. Both are in the Start menu too."
    )


def _remove_windows(report, *, home, env, run) -> None:
    paths = _windows_paths(home, env)
    for key in ("menu", "call", "startup", "icon"):
        _unlink(paths[key], report)
    try:
        _powershell(
            "$d = Join-Path ([Environment]::GetFolderPath('Desktop')) 'JARVIS.lnk'\n"
            "if (Test-Path $d) { Remove-Item $d }\n",
            run,
        )
        report.removed.append("Desktop\\JARVIS.lnk")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass


# --- entry points -----------------------------------------------------------

_INSTALLERS = {"linux": _install_linux, "darwin": _install_mac, "win32": _install_windows}
_REMOVERS = {"linux": _remove_linux, "darwin": _remove_mac, "win32": _remove_windows}


def _family(platform: str) -> str:
    return "linux" if platform.startswith(("linux", "freebsd")) else platform


def install(
    *,
    autostart: bool = False,
    desktop: bool = True,
    platform: str = sys.platform,
    home: Path | None = None,
    env=None,
    run=subprocess.run,
) -> Report:
    """Create launchers for this OS. Safe to run again; it overwrites its own files."""
    report = Report()
    family = _family(platform)
    if family not in _INSTALLERS:
        report.notes.append(f"No launcher support for {platform}; run `jarvis` instead.")
        return report
    _INSTALLERS[family](
        report,
        home=home or Path.home(),
        env=os.environ if env is None else env,
        run=run,
        autostart=autostart,
        desktop=desktop,
    )
    return report


def remove(
    *, platform: str = sys.platform, home: Path | None = None, env=None, run=subprocess.run
) -> Report:
    """Delete every launcher :func:`install` can create."""
    report = Report()
    family = _family(platform)
    if family in _REMOVERS:
        _REMOVERS[family](
            report,
            home=home or Path.home(),
            env=os.environ if env is None else env,
            run=run,
        )
    return report


def _write(path: Path, data: bytes, report: Report, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mode is not None:
        path.chmod(mode)
    report.created.append(str(path))


def _unlink(path: Path, report: Report) -> None:
    try:
        path.unlink()
        report.removed.append(str(path))
    except FileNotFoundError:
        pass
