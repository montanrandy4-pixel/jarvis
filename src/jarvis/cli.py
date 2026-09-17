"""Command line entry points for JARVIS."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .agent import Agent
from .config import VOICE_MAX_TOKENS, Config
from .memory import MemoryStore
from .tools import build_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="A voice-driven AI assistant powered by Claude.",
    )
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    parser.add_argument("--model", help="Model id (default: claude-opus-5).")
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="How hard Claude thinks. Voice defaults to low for latency.",
    )
    parser.add_argument(
        "--shell",
        choices=["off", "confirm", "on"],
        help="Shell access policy (default: confirm).",
    )
    parser.add_argument(
        "--no-web", action="store_true", help="Disable the web search tool."
    )
    parser.add_argument("--debug", action="store_true", help="Verbose logging.")

    subs = parser.add_subparsers(dest="command")

    listen = subs.add_parser("listen", help="Hands-free voice mode (default).")
    listen.add_argument("--wake-word", help='Wake phrase (default: "hey jarvis").')
    listen.add_argument("--stt-model", help="faster-whisper model (default: base.en).")
    listen.add_argument(
        "--tts",
        dest="tts_backend",
        choices=["auto", "piper", "pyttsx3", "say", "espeak", "none"],
        help="Speech synthesis backend.",
    )
    listen.add_argument("--voice", dest="voice_name", help="Backend-specific voice id.")
    listen.add_argument(
        "--no-barge-in",
        action="store_true",
        help="Do not let speech interrupt a reply (use on open speakers).",
    )

    subs.add_parser("chat", help="Talk to JARVIS by typing instead.")

    ask = subs.add_parser("ask", help="Ask one question and print the answer.")
    ask.add_argument("question", nargs="+")

    subs.add_parser("doctor", help="Check what voice mode can and cannot do here.")

    mem = subs.add_parser("memory", help="Inspect what JARVIS remembers.")
    mem.add_argument(
        "action", nargs="?", default="list", choices=["list", "add", "forget", "clear"]
    )
    mem.add_argument("value", nargs="*", help="Text to add, or an id to forget.")
    return parser


def _config_from(args) -> Config:
    overrides = {
        "model": getattr(args, "model", None),
        "effort": getattr(args, "effort", None),
        "shell": getattr(args, "shell", None),
        "wake_word": getattr(args, "wake_word", None),
        "stt_model": getattr(args, "stt_model", None),
        "tts_backend": getattr(args, "tts_backend", None),
        "voice_name": getattr(args, "voice_name", None),
    }
    if getattr(args, "no_web", False):
        overrides["allow_web"] = False
    if getattr(args, "no_barge_in", False):
        overrides["barge_in"] = False
    return Config.load(**overrides)


def _make_agent_factory(config: Config, *, voice: bool):
    """Build an Agent once the confirmation callback is known."""
    memory = MemoryStore(config.memory_path)

    def factory(confirm):
        registry = build_registry(config, memory, confirm=confirm)
        return Agent(
            config,
            registry,
            memory,
            voice=voice,
            on_tool=None if voice else _print_tool,
        )

    return factory


def _print_tool(label: str, result) -> None:
    mark = "\N{CROSS MARK}" if result.is_error else "\N{HEAVY CHECK MARK}"
    print(f"  {mark} {label}", flush=True)


def cmd_listen(args) -> int:
    from .voice import run_voice

    config = _config_from(args)
    return run_voice(config, _make_agent_factory(config, voice=True))


def cmd_chat(args) -> int:
    config = _config_from(args)
    # Typed conversation is not read aloud, so let it think and answer properly.
    if getattr(args, "effort", None) is None:
        config.effort = "high"
    if config.max_tokens == VOICE_MAX_TOKENS:
        config.max_tokens = 16_000

    def confirm(_name: str, summary: str) -> bool:
        try:
            answer = input(f"  Allow: {summary}? [y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}

    agent = _make_agent_factory(config, voice=False)(confirm)
    print(f"JARVIS ({config.model}). Ctrl-C or /exit to leave, /help for commands.")
    while True:
        try:
            said = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not said:
            continue
        if said.startswith("/"):
            if _slash(said, agent):
                break
            continue
        print("\njarvis > ", end="", flush=True)
        turn = agent.reply(said, on_text=lambda chunk: print(chunk, end="", flush=True))
        print()
        if turn.refusal or turn.error:
            print(f"[{turn.refusal or turn.error}]")
    path = agent.save_transcript()
    if path:
        print(f"Transcript saved to {path}")
    return 0


def _slash(command: str, agent: Agent) -> bool:
    """Handle a /command. Returns True if the session should end."""
    name = command.split()[0].lower()
    if name in {"/exit", "/quit"}:
        return True
    if name == "/reset":
        agent.reset()
        print("Conversation cleared.")
    elif name == "/memory":
        items = agent.memory.all()
        print("\n".join(m.describe() for m in items) or "Nothing remembered yet.")
    elif name == "/tools":
        print(", ".join(sorted(agent.registry.tools)))
    elif name == "/save":
        print(f"Saved to {agent.save_transcript()}")
    elif name == "/help":
        print("/reset  /memory  /tools  /save  /exit")
    else:
        print(f"Unknown command {name}. Try /help.")
    return False


def cmd_ask(args) -> int:
    config = _config_from(args)
    agent = _make_agent_factory(config, voice=False)(lambda _n, _s: False)
    turn = agent.reply(
        " ".join(args.question),
        on_text=lambda chunk: print(chunk, end="", flush=True),
    )
    print()
    if turn.refusal or turn.error:
        print(f"[{turn.refusal or turn.error}]", file=sys.stderr)
        return 1
    return 0


def cmd_memory(args) -> int:
    config = _config_from(args)
    memory = MemoryStore(config.memory_path)
    if args.action == "add":
        if not args.value:
            print("Nothing to remember.", file=sys.stderr)
            return 1
        print(f"Saved: {memory.add(' '.join(args.value)).describe()}")
    elif args.action == "forget":
        for item_id in args.value:
            print(("Deleted " if memory.forget(item_id) else "No such memory ") + item_id)
    elif args.action == "clear":
        for item in memory.all():
            memory.forget(item.id)
        print("Memory cleared.")
    else:
        items = memory.all()
        print("\n".join(m.describe() for m in items) or "Nothing remembered yet.")
        print(f"\n({config.memory_path})")
    return 0


def cmd_doctor(args) -> int:
    """Report what is installed, so a broken mic is obvious before you talk."""
    import os
    import shutil

    config = _config_from(args)
    print(f"jarvis {__version__}\n")

    def check(label: str, ok: bool, detail: str = "") -> bool:
        mark = "\N{HEAVY CHECK MARK}" if ok else "\N{CROSS MARK}"
        print(f"  {mark} {label}" + (f" -- {detail}" if detail else ""))
        return ok

    print("Credentials")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    profile = shutil.which("ant")
    check(
        "Anthropic credentials",
        has_key or bool(profile),
        "ANTHROPIC_API_KEY is set"
        if has_key
        else "no API key; `ant auth login` profile may still work"
        if profile
        else "set ANTHROPIC_API_KEY or run `ant auth login`",
    )

    print("\nSpeech input")
    audio_ok = _importable("sounddevice") and _importable("numpy")
    check("microphone (sounddevice, numpy)", audio_ok, "pip install 'jarvis[voice]'")
    check("voice activity (webrtcvad)", _importable("webrtcvad"), "energy fallback")
    check("recognition (faster-whisper)", _importable("faster_whisper"), config.stt_model)
    check("wake word (openwakeword)", _importable("openwakeword"), config.wake_word)
    if audio_ok:
        try:
            import sounddevice as sd

            default_in = sd.query_devices(kind="input")["name"]
            check("input device", True, default_in)
        except Exception as exc:
            check("input device", False, str(exc))

    print("\nSpeech output")
    from .audio.tts import make_speaker

    speaker = make_speaker(config)
    check(
        f"synthesis ({speaker.name})",
        speaker.name != "print",
        "no audio backend found; replies will be printed"
        if speaker.name == "print"
        else "",
    )

    print("\nSettings")
    print(f"  model        {config.model} (effort {config.effort})")
    print(f"  shell        {config.shell}")
    print(f"  web search   {'on' if config.allow_web else 'off'}")
    print(f"  workspace    {', '.join(str(p) for p in config.workspace_roots())}")
    print(f"  state        {config.state_dir}")
    ready = audio_ok and _importable("faster_whisper")
    print(
        "\nVoice mode is ready."
        if ready
        else "\nVoice mode needs more packages: pip install 'jarvis[voice]'"
        "\nIn the meantime, `jarvis chat` works anywhere."
    )
    return 0 if ready else 1


def _importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        None: cmd_listen,  # Bare `jarvis` starts listening.
        "listen": cmd_listen,
        "chat": cmd_chat,
        "ask": cmd_ask,
        "memory": cmd_memory,
        "doctor": cmd_doctor,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
