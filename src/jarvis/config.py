"""Configuration for JARVIS.

Settings come from three places, later ones winning: built-in defaults, a TOML
file at ``~/.config/jarvis/config.toml``, and ``JARVIS_*`` environment
variables. Everything has a working default, so a bare ``jarvis`` run with no
configuration at all does the right thing.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

# Replies are read aloud, so the assistant should not be able to monologue for
# thousands of tokens.
VOICE_REPLY_TOKENS = 1024

CONFIG_PATH = Path(
    os.environ.get("JARVIS_CONFIG", "~/.config/jarvis/config.toml")
).expanduser()


def _default_state_dir() -> Path:
    return Path(
        os.environ.get("JARVIS_STATE_DIR", "~/.local/state/jarvis")
    ).expanduser()


@dataclass
class Config:
    """Everything JARVIS needs to know about how it should behave."""

    # --- Model ---
    # "ollama" runs the model on this machine. "openai" is any server speaking
    # the OpenAI chat-completions protocol (llama.cpp, vLLM, LM Studio, ...).
    backend: str = "ollama"
    model: str = "llama3.1:8b"
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.6
    context_tokens: int = 8192
    max_reply_tokens: int = VOICE_REPLY_TOKENS
    # Keep the model loaded between questions; reloading costs seconds a turn.
    keep_alive: str = "10m"

    # --- Persona ---
    name: str = "JARVIS"
    address_user_as: str = "sir"
    user_name: str = ""

    # --- Voice I/O ---
    wake_word: str = "hey jarvis"
    stt_model: str = "base.en"
    stt_device: str = "auto"
    tts_backend: str = "auto"
    voice_name: str = ""
    speaking_rate: int = 180
    input_device: str = ""
    output_device: str = ""
    # Let the user interrupt a spoken reply by talking over it.
    barge_in: bool = True
    # Seconds of silence that end a user's utterance.
    silence_timeout: float = 1.0
    # Hard cap on a single utterance, so a stuck mic cannot record forever.
    max_utterance_seconds: float = 30.0
    # After a reply, keep listening this long without the wake word so
    # follow-ups feel like a conversation. 0 disables.
    followup_window: float = 8.0

    # --- Tools ---
    # off: no shell at all. confirm: ask before anything that writes.
    # on: run whatever the model asks for (use with care).
    shell: str = "confirm"
    shell_timeout: float = 60.0
    allow_web: bool = True
    # Directories the file tools may touch. Empty means the home directory.
    workspace: list[str] = field(default_factory=list)

    # --- App ---
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    # Open in a Chrome or Edge app window (no tabs or address bar) when one is
    # installed, rather than a tab in the default browser.
    app_window: bool = True

    # --- Storage ---
    state_dir: Path = field(default_factory=_default_state_dir)

    @property
    def memory_path(self) -> Path:
        return self.state_dir / "memory.json"

    @property
    def transcript_dir(self) -> Path:
        return self.state_dir / "transcripts"

    def workspace_roots(self) -> list[Path]:
        roots = [Path(p).expanduser().resolve() for p in self.workspace]
        return roots or [Path.home().resolve()]

    @classmethod
    def load(cls, **overrides) -> "Config":
        """Build a config from the TOML file, the environment, then kwargs."""
        values: dict[str, object] = {}
        values.update(_from_toml())
        values.update(_from_env())
        values.update({k: v for k, v in overrides.items() if v is not None})

        known = {f.name: f for f in fields(cls)}
        clean: dict[str, object] = {}
        for key, raw in values.items():
            spec = known.get(key)
            if spec is None:
                continue  # Ignore unknown keys rather than crashing on a typo.
            clean[key] = _coerce(raw, spec.type)
        return cls(**clean)


def _from_toml() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    with CONFIG_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    # Allow either a flat table or a [jarvis] section.
    return data.get("jarvis", data)


def _from_env() -> dict:
    out: dict[str, str] = {}
    for spec in fields(Config):
        raw = os.environ.get(f"JARVIS_{spec.name.upper()}")
        if raw is not None and raw != "":
            out[spec.name] = raw
    return out


def _coerce(raw, target) -> object:
    """Turn a string from TOML or the environment into the field's type."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if target is bool or target == "bool":
        return text.lower() in {"1", "true", "yes", "on"}
    if target is int or target == "int":
        return int(text)
    if target is float or target == "float":
        return float(text)
    if target is Path or target == "Path":
        return Path(text).expanduser()
    if "list" in str(target):
        return [part.strip() for part in text.split(os.pathsep) if part.strip()]
    return text
