"""Fixtures shared by the JARVIS tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.config import Config  # noqa: E402
from jarvis.memory import MemoryStore  # noqa: E402


@pytest.fixture
def config(tmp_path) -> Config:
    return Config.load(
        state_dir=tmp_path / "state",
        workspace=[str(tmp_path / "work")],
        shell="confirm",
        allow_web=False,
    )


@pytest.fixture
def memory(config) -> MemoryStore:
    return MemoryStore(config.memory_path)


@pytest.fixture
def workspace(config) -> Path:
    root = config.workspace_roots()[0]
    root.mkdir(parents=True, exist_ok=True)
    return root


__all__ = ["BackendError", "Completion", "FakeBackend", "ToolCall", "calls_tool", "says"]
