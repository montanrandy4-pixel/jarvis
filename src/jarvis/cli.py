"""Command line entry points for JARVIS."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import VOICE_REPLY_TOKENS, Config
from .memory import MemoryStore
from .tools import build_registry


def _common_flags() -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand.

    Defaults are suppressed so that `jarvis --model x app` is not undone by the
    subparser re-parsing --model with a default of None.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--backend",
        choices=["ollama", "openai"],
        default=argparse.SUPPRESS,
        help="Where the model runs (default: ollama, on this machine).",
    )
    common.add_argument(
        "--model", default=argparse.SUPPRESS, help="Model name (default: llama3.1:8b)."
    )
    common.add_argument(
        "--base-url",
        dest="base_url",
        default=argparse.SUPPRESS,
        help="Model server URL, for a different port or a remote server.",
    )
    common.add_argument(
        "--shell",
        choices=["off", "confirm", "on"],
        default=argparse.SUPPRESS,
        help="Shell access policy (default: confirm).",
    )
    common.add_argument(
        "--no-web",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable the web search and page-reading tools.",
    )
    common.add_argument(
        "--debug", action="store_true", default=argparse.SUPPRESS,
        help="Verbose logging.",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_flags()
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="A voice-driven AI assistant that runs on your own machine.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")

    subs = parser.add_subparsers(dest="command")

    app = subs.add_parser("app", help="Open the JARVIS app (default).", parents=[common])
    app.add_argument("--port", type=int, default=argparse.SUPPRESS,
                     help="Port to serve on (default: 8765).")
    app.add_argument("--host", default=argparse.SUPPRESS,
                     help="Interface to bind (default: 127.0.0.1).")
    app.add_argument("--no-browser", action="store_true", default=argparse.SUPPRESS,
                     help="Do not open a browser window.")

    listen = subs.add_parser(
        "listen", help="Voice mode in the terminal, with no browser.", parents=[common]
    )
    listen.add_argument("--wake-word", default=argparse.SUPPRESS,
                        help='Wake phrase (default: "hey jarvis").')
    listen.add_argument("--stt-model", default=argparse.SUPPRESS,
                        help="faster-whisper model (default: base.en).")
    listen.add_argument(
        "--tts", dest="tts_backend", default=argparse.SUPPRESS,
        choices=["auto", "piper", "pyttsx3", "say", "espeak", "none"],
        help="Speech synthesis backend.",
    )
    listen.add_argument("--voice", dest="voice_name", default=argparse.SUPPRESS,
                        help="Backend-specific voice id.")
    listen.add_argument("--no-barge-in", action="store_true", default=argparse.SUPPRESS,
                        help="Do not let speech interrupt a reply.")

    subs.add_parser("chat", help="Talk to JARVIS by typing.", parents=[common])

    ask = subs.add_parser(
        "ask", help="Ask one question and print the answer.", parents=[common]
    )
    ask.add_argument("question", nargs="+")

    subs.add_parser(
        "doctor", help="Check what works on this machine.", parents=[common]
    )

    mem = subs.add_parser(
        "memory", help="Inspect what JARVIS remembers.", parents=[common]
    )
    mem.add_argument(
        "action", nargs="?", default="list", choices=["list", "add", "forget", "clear"]
    )
    mem.add_argument("value", nargs="*", help="Text to add, or an id to forget.")
    return parser


def _config_from(args) -> Config:
    overrides = {
        "backend": getattr(args, "backend", None),
        "model": getattr(args, "model", None),
        "base_url": getattr(args, "base_url", None),
        "host": getattr(args, "host", None),
        "port": getattr(args, "port", None),
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
    if getattr(args, "no_browser", False):
        overrides["open_browser"] = False
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


def cmd_app(args) -> int:
    from .server import serve

    return serve(_config_from(args))


def cmd_listen(args) -> int:
    from .voice import run_voice

    config = _config_from(args)
    return run_voice(config, _make_agent_factory(config, voice=True))


def cmd_chat(args) -> int:
    config = _config_from(args)
    # A typed conversation is not read aloud, so it can afford longer answers.
    if config.max_reply_tokens == VOICE_REPLY_TOKENS:
        config.max_reply_tokens = 4096

    def confirm(_name: str, summary: str) -> bool:
        try:
            answer = input(f"  Allow: {summary}? [y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}

    agent = _make_agent_factory(config, voice=False)(confirm)
    print(
        f"JARVIS ({agent.backend.description}). "
        "Ctrl-C or /exit to leave, /help for commands."
    )
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
        if turn.error:
            print(f"[{turn.error}]")
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
    if turn.error:
        print(f"[{turn.error}]", file=sys.stderr)
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
    """Report what works on this machine, so problems are obvious up front."""
    config = _config_from(args)
    print(f"jarvis {__version__}\n")
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> bool:
        mark = "\N{HEAVY CHECK MARK}" if passed else "\N{CROSS MARK}"
        print(f"  {mark} {label}" + (f" -- {detail}" if detail else ""))
        return passed

    print("Model")
    from .backends import BackendError, make_backend

    try:
        backend = make_backend(config)
    except BackendError as exc:
        check("backend", False, str(exc))
        return 1
    ready, detail = backend.health()
    ok &= check(f"{backend.name}", ready, detail)
    if ready:
        installed = backend.models()
        if installed:
            print(f"      available: {', '.join(installed[:8])}")
        supports = getattr(backend, "supports_tools", lambda: None)()
        if supports is False:
            ok &= check(
                "tool calling",
                False,
                f"{backend.model} cannot call tools; try llama3.1:8b or qwen2.5:7b",
            )
        elif supports:
            check("tool calling", True, "supported")

    print("\nApp")
    check("web assets", (Path(__file__).parent / "web" / "index.html").is_file())
    print(f"      http://{config.host}:{config.port}/")

    print("\nTerminal voice mode (optional -- the app uses your browser instead)")
    audio_ok = _importable("sounddevice") and _importable("numpy")
    check("microphone (sounddevice, numpy)", audio_ok, "pip install 'jarvis[voice]'")
    check("recognition (faster-whisper)", _importable("faster_whisper"), config.stt_model)
    check("wake word (openwakeword)", _importable("openwakeword"), config.wake_word)
    from .audio.tts import make_speaker

    speaker = make_speaker(config)
    check(f"synthesis ({speaker.name})", speaker.name != "print", "")

    print("\nSettings")
    print(f"  shell        {config.shell}")
    print(f"  web search   {'on' if config.allow_web else 'off'}")
    print(f"  workspace    {', '.join(str(p) for p in config.workspace_roots())}")
    print(f"  state        {config.state_dir}")

    print("\nReady." if ok else "\nNot ready -- see the failures above.")
    return 0 if ok else 1


def _importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "debug", False) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        None: cmd_app,  # Bare `jarvis` opens the app.
        "app": cmd_app,
        "listen": cmd_listen,
        "chat": cmd_chat,
        "ask": cmd_ask,
        "memory": cmd_memory,
        "doctor": cmd_doctor,
    }
    try:
        return handlers[getattr(args, "command", None)](args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
