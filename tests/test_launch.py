"""Opening JARVIS: app windows, launchers on each OS, and the HUD's telemetry."""

from __future__ import annotations

import plistlib
import struct
import sys
from pathlib import Path

import pytest

from jarvis import launcher, shortcuts
from jarvis.tools import system


class TestAddresses:
    @pytest.mark.parametrize(
        "host, expected",
        [
            ("127.0.0.1", "http://127.0.0.1:8765/"),
            ("0.0.0.0", "http://127.0.0.1:8765/"),
            ("localhost", "http://localhost:8765/"),
            ("::1", "http://[::1]:8765/"),
        ],
    )
    def test_local_url(self, host, expected):
        assert launcher.local_url(host, 8765) == expected

    def test_nothing_running_on_a_closed_port(self):
        import socket

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        assert launcher.running_instance(f"http://127.0.0.1:{port}/", timeout=0.5) is False
        assert launcher.stop_instance(f"http://127.0.0.1:{port}/", timeout=0.5) is False


class TestAppWindow:
    def test_prefers_a_chromium_browser_in_app_mode(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            launcher.shutil, "which",
            lambda name: "/usr/bin/chromium" if name == "chromium" else None,
        )

        command = launcher.app_window_command("http://127.0.0.1:8765/")

        assert command == ["/usr/bin/chromium", "--app=http://127.0.0.1:8765/"]

    def test_falls_back_to_the_default_browser(self, monkeypatch):
        opened = []
        monkeypatch.setattr(launcher, "app_window_command", lambda url: None)
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)

        assert launcher.open_window("http://127.0.0.1:8765/") == "browser"
        assert opened == ["http://127.0.0.1:8765/"]

    def test_app_window_can_be_turned_off(self, monkeypatch):
        monkeypatch.setattr(
            launcher, "app_window_command",
            lambda url: pytest.fail("should not look for a browser"),
        )
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: True)

        assert launcher.open_window("http://x/", app_window=False) == "browser"

    def test_a_browser_that_will_not_start_falls_back(self, monkeypatch):
        monkeypatch.setattr(launcher, "app_window_command", lambda url: ["/nonexistent/chrome"])
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: True)

        assert launcher.open_window("http://x/") == "browser"


class Recorder:
    """Stands in for subprocess.run and remembers what it was asked to run."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return type("Done", (), {"returncode": self.returncode, "stdout": "", "stderr": ""})()


class TestLinuxLaunchers:
    def test_menu_entry_desktop_icon_and_autostart(self, tmp_path):
        (tmp_path / "Desktop").mkdir()
        run = Recorder(returncode=1)  # No xdg-user-dir: fall back to ~/Desktop.

        report = shortcuts.install(
            autostart=True, platform="linux", home=tmp_path, env={}, run=run
        )

        menu = tmp_path / ".local/share/applications/jarvis.desktop"
        auto = tmp_path / ".config/autostart/jarvis.desktop"
        desk = tmp_path / "Desktop/jarvis.desktop"
        assert {str(menu), str(auto), str(desk)} <= set(report.created)
        entry = menu.read_text()
        assert f"Exec={sys.executable} -m jarvis app\n" in entry
        assert "Icon=" + str(tmp_path / ".local/share/icons") in entry
        assert Path(entry.split("Icon=")[1].split("\n")[0]).is_file()
        assert "app --no-browser" in auto.read_text()
        assert desk.stat().st_mode & 0o111

    def test_remove_undoes_install(self, tmp_path):
        run = Recorder(returncode=1)
        shortcuts.install(autostart=True, platform="linux", home=tmp_path, env={}, run=run)

        report = shortcuts.remove(platform="linux", home=tmp_path, env={}, run=run)

        assert len(report.removed) == 3
        assert not (tmp_path / ".local/share/applications/jarvis.desktop").exists()

    def test_saying_no_later_turns_autostart_off(self, tmp_path):
        shortcuts.install(autostart=True, platform="linux", home=tmp_path, env={}, run=Recorder(1))

        report = shortcuts.install(
            autostart=False, platform="linux", home=tmp_path, env={}, run=Recorder(1)
        )

        assert not (tmp_path / ".config/autostart/jarvis.desktop").exists()
        assert str(tmp_path / ".config/autostart/jarvis.desktop") in report.removed

    def test_xdg_directories_are_respected(self, tmp_path):
        env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CONFIG_HOME": str(tmp_path / "cfg")}

        shortcuts.install(platform="linux", home=tmp_path, env=env, run=Recorder(1))

        assert (tmp_path / "data/applications/jarvis.desktop").is_file()

    def test_paths_with_spaces_are_quoted(self):
        entry = shortcuts.desktop_entry(
            ["/home/a b/venv/bin/python", "-m", "jarvis"], icon=Path("/i.png")
        )
        assert 'Exec="/home/a b/venv/bin/python" -m jarvis' in entry


class TestMacLaunchers:
    def test_app_bundle_and_login_agent(self, tmp_path):
        report = shortcuts.install(
            autostart=True, platform="darwin", home=tmp_path,
            env={"PATH": "/opt/homebrew/bin:/usr/bin"}, run=Recorder(),
        )

        app = tmp_path / "Applications/JARVIS.app/Contents"
        info = plistlib.loads((app / "Info.plist").read_bytes())
        assert info["CFBundleExecutable"] == "JARVIS"
        script = app / "MacOS/JARVIS"
        assert script.stat().st_mode & 0o111
        text = script.read_text()
        assert "nohup" in text and "-m jarvis app" in text
        assert "/opt/homebrew/bin" in text
        icon = (app / "Resources/JARVIS.icns").read_bytes()
        assert icon[:4] == b"icns"
        assert struct.unpack(">I", icon[4:8])[0] == len(icon)

        agent = plistlib.loads(
            (tmp_path / "Library/LaunchAgents/local.jarvis.assistant.plist").read_bytes()
        )
        assert agent["RunAtLoad"] is True
        assert agent["ProgramArguments"][-2:] == ["app", "--no-browser"]
        assert str(tmp_path / "Applications/JARVIS.app") in report.created

    def test_remove_deletes_the_bundle(self, tmp_path):
        shortcuts.install(autostart=True, platform="darwin", home=tmp_path, env={}, run=Recorder())

        shortcuts.remove(platform="darwin", home=tmp_path, env={}, run=Recorder())

        assert not (tmp_path / "Applications/JARVIS.app").exists()
        assert not (tmp_path / "Library/LaunchAgents/local.jarvis.assistant.plist").exists()


class TestWindowsLaunchers:
    def _install(self, tmp_path, **options):
        run = Recorder()
        env = {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}
        report = shortcuts.install(platform="win32", home=tmp_path, env=env, run=run, **options)
        [argv] = run.calls
        return report, argv[argv.index("-Command") + 1]

    def test_start_menu_shortcut_owns_the_hotkey(self, tmp_path):
        report, script = self._install(tmp_path)

        assert script.count("$s.Hotkey = 'CTRL+ALT+J'") == 1
        start_menu = script.index("$programs")
        assert script.index("Hotkey") > start_menu
        assert "-m jarvis app'" in script
        assert "GetFolderPath('Desktop')" in script
        assert "Startup" not in script
        assert any("Hotkey" in note or "Ctrl+Alt+J" in note for note in report.notes)

    def test_autostart_goes_in_the_startup_folder(self, tmp_path):
        _, script = self._install(tmp_path, autostart=True, desktop=False)

        assert "(Join-Path $programs 'Startup')" in script
        assert "-m jarvis app --no-browser" in script
        assert "GetFolderPath('Desktop')" not in script

    def test_the_icon_is_a_real_ico(self, tmp_path):
        self._install(tmp_path)

        ico = (tmp_path / "Local/JARVIS/jarvis.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", ico[:6])
        size, offset = struct.unpack("<II", ico[14:22])
        assert (reserved, kind, count) == (0, 1, 1)
        assert ico[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + size == len(ico)

    def test_single_quotes_in_paths_survive_powershell(self):
        script = shortcuts.windows_script(
            target=r"C:\Users\O'Brien\python.exe", icon=Path("i.ico"),
            programs=Path("P"), desktop=False, autostart=False,
        )
        assert r"'C:\Users\O''Brien\python.exe'" in script

    def test_a_powershell_failure_is_reported(self, tmp_path):
        env = {"APPDATA": str(tmp_path), "LOCALAPPDATA": str(tmp_path)}
        with pytest.raises(RuntimeError):
            shortcuts.install(platform="win32", home=tmp_path, env=env, run=Recorder(1))


class TestTelemetry:
    def test_snapshot_has_every_reading(self):
        snap = system.snapshot()

        assert set(snap) >= {"host", "os", "cpus", "cpu_pct", "uptime_s", "memory", "disk", "battery"}

    def test_the_tool_copes_with_missing_readings(self):
        text = system.describe(
            {
                "host": "h", "os": "Plan 9", "cpus": 2, "cpu_pct": None,
                "uptime_s": None, "memory": None, "disk": None, "battery": None,
            }
        )

        assert "Memory: unknown" in text
        assert "Battery: no battery detected" in text
        assert "Uptime: unknown" in text
