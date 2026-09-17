"""Configuration layering and path handling."""

from __future__ import annotations

from pathlib import Path

from jarvis.config import Config


def test_defaults_are_usable_without_any_setup():
    config = Config.load()

    assert config.backend == "ollama"  # Runs on this machine by default.
    assert config.model == "llama3.1:8b"
    assert config.shell == "confirm"


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "computer")

    config = Config.load()

    assert config.model == "qwen2.5:7b"
    assert config.wake_word == "computer"


def test_explicit_arguments_beat_the_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL", "from-env")

    assert Config.load(model="from-args").model == "from-args"


def test_types_are_coerced_from_strings(monkeypatch):
    monkeypatch.setenv("JARVIS_BARGE_IN", "false")
    monkeypatch.setenv("JARVIS_SILENCE_TIMEOUT", "2.5")
    monkeypatch.setenv("JARVIS_SPEAKING_RATE", "200")

    config = Config.load()

    assert config.barge_in is False
    assert config.silence_timeout == 2.5
    assert config.speaking_rate == 200


def test_unknown_settings_are_ignored_not_fatal(monkeypatch):
    monkeypatch.setenv("JARVIS_NOT_A_REAL_SETTING", "x")

    assert Config.load().model  # Still loads.


def test_a_workspace_list_comes_from_a_path_separated_string(monkeypatch):
    monkeypatch.setenv("JARVIS_WORKSPACE", "/tmp:/var/tmp")

    roots = Config.load().workspace_roots()

    assert Path("/tmp") in roots and Path("/var/tmp") in roots


def test_workspace_defaults_to_home():
    assert Config.load(workspace=[]).workspace_roots() == [Path.home().resolve()]


def test_state_paths_hang_off_the_state_directory(tmp_path):
    config = Config.load(state_dir=tmp_path)

    assert config.memory_path == tmp_path / "memory.json"
    assert config.transcript_dir == tmp_path / "transcripts"
