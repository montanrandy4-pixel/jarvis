# JARVIS

A voice-driven AI assistant powered by Claude. Say the wake word, ask for
something, and it answers out loud — running tools on your machine when the
answer requires actually looking.

```
$ jarvis
Listening. Say "hey jarvis" to wake me.
🔊  Good evening, sir. I'm listening.

> hey jarvis, how much disk have I got left
🔊  About 47 gigabytes free of 256, so you're fine for now.

> set a timer for ten minutes for the pasta
🔊  Timer set for the pasta, going off in 10 minutes.
```

Speech recognition and the wake word run locally. Only the transcribed text is
sent to the API.

## Install

```bash
git clone https://github.com/montanrandy4-pixel/jarvis && cd jarvis
pip install -e '.[voice]'      # or just `pip install -e .` for typed chat only
export ANTHROPIC_API_KEY=sk-ant-...
jarvis doctor                  # says what works on this machine
jarvis                         # start listening
```

`jarvis doctor` is the first thing to run: it checks credentials, the
microphone, the speech engines and the wake-word model, and tells you exactly
what is missing.

## Commands

| Command | What it does |
|---|---|
| `jarvis` / `jarvis listen` | Hands-free voice mode |
| `jarvis chat` | Type instead of talking (works with no audio hardware) |
| `jarvis ask "..."` | One question, answer to stdout |
| `jarvis memory` | Show, add or delete what JARVIS remembers |
| `jarvis doctor` | Check the setup |

Useful flags: `--model`, `--effort low|medium|high|xhigh|max`,
`--shell off|confirm|on`, `--no-web`, `--wake-word`, `--tts`, `--no-barge-in`.

## How voice mode works

```
mic ──► wake word ──► record until silence ──► whisper ──► Claude ──► sentences ──► speaker
        (local)        (VAD)                   (local)      (+tools)    (streamed)
```

A few details that matter in practice:

- **Replies start before they finish.** Text is cut at sentence boundaries as it
  streams from the model, so the first sentence is spoken while the rest is
  still being generated.
- **Barge-in.** Talk over a reply and it stops mid-sentence. Use headphones, or
  pass `--no-barge-in` on open speakers — without echo cancellation, JARVIS can
  otherwise hear itself and interrupt its own answer.
- **Follow-ups need no wake word.** After a reply it keeps listening for about
  eight seconds (`followup_window`).
- **Some things never reach the API.** "stop", "never mind" and "goodbye" are
  handled locally, so interrupting is instant.
- **Timers announce themselves** out loud whenever they fire.

## Tools

| Tool | Notes |
|---|---|
| `run_shell` | Gated by the `shell` setting (see below) |
| `read_file`, `write_file`, `list_directory` | Confined to the workspace roots |
| `remember`, `recall`, `forget` | Durable facts in `memory.json` |
| `set_timer`, `list_timers`, `cancel_timer` | Spoken when they fire |
| `system_status` | Uptime, load, memory, disk, battery |
| `web_search` | Server-side; disable with `--no-web` |

### Safety

The assistant can run commands on your machine, so two gates are on by default:

- **`shell = confirm`** — read-only commands (`ls`, `df`, `git status`, …) run
  freely; anything that might change something is read back to you and needs a
  spoken "yes". The classifier errs toward asking: anything it does not
  recognise, and anything with a pipe, redirect or substitution, counts as a
  write. `shell = off` removes the tool; `shell = on` stops asking.
- **Workspace confinement** — the file tools only touch `workspace` (your home
  directory by default), and paths are resolved before the check, so `../..`
  cannot climb out.

Memory, transcripts and settings live under `~/.local/state/jarvis`.

## Configuration

Environment variables (`JARVIS_MODEL`, `JARVIS_WAKE_WORD`, …) or
`~/.config/jarvis/config.toml`:

```toml
[jarvis]
model = "claude-opus-5"
effort = "low"              # voice trades depth for latency; chat uses high
wake_word = "hey jarvis"
stt_model = "small.en"      # bigger whisper model, better with accents
tts_backend = "piper"
voice_name = "/path/to/en_GB-alan-medium.onnx"
shell = "confirm"
workspace = ["~/projects", "~/Documents"]
address_user_as = "sir"
```

See `.env.example` for the full list.

### Speech engines

Picked automatically, best first. **Input:** openWakeWord (it ships a trained
"hey jarvis" model) for waking, webrtcvad for endpointing, faster-whisper for
recognition — each degrades to a working fallback if absent, down to push-to-talk
and an energy-based voice detector. **Output:** piper (neural, best) → pyttsx3 →
macOS `say` → espeak-ng → printing to the terminal. With no audio stack at all,
`jarvis chat` still works everywhere.

## Adding a tool

```python
from jarvis.tools import Tool, ToolResult

Tool(
    name="lights",
    description="Turn a room's lights on or off.",
    input_schema={
        "type": "object",
        "properties": {
            "room": {"type": "string"},
            "on": {"type": "boolean"},
        },
        "required": ["room", "on"],
        "additionalProperties": False,
    },
    handler=lambda room, on: ToolResult(f"{room} lights {'on' if on else 'off'}"),
    mutates=True,                       # ask before doing it
    summarize=lambda a: f"turn the {a['room']} lights "
                        f"{'on' if a['on'] else 'off'}",
)
```

Register it in `jarvis/tools/__init__.py:build_registry`. Inputs are validated
against the schema before the handler runs, and a handler that raises becomes an
error the model can recover from rather than a crash.

## Development

```bash
pip install -e '.[dev]'
pytest                    # 128 tests, no API key or microphone required
```

The agent loop is tested against a scripted fake client (`tests/conftest.py`),
which covers tool round-trips, refusals, truncated tool inputs, cancellation and
the prompt-cache layout.

### Layout

```
src/jarvis/
  agent.py      streaming loop, tool execution, error handling
  persona.py    system prompt (stable half cached, volatile half not)
  memory.py     durable facts
  config.py     defaults → TOML → environment → flags
  voice.py      wake / listen / think / speak
  cli.py        commands
  audio/        mic + VAD, whisper, wake word, speech synthesis
  tools/        registry, validation, and the built-in tools
```

Notes on the API usage: adaptive thinking with a low effort setting (voice
latency), prompt caching with the persona behind a cache breakpoint and the
clock after it, server-side context editing so long sessions do not grow without
bound, and server-side refusal fallbacks. Any of those the account does not
support is dropped on the first rejection and the request retried, rather than
failing the turn.

## Limitations

- No acoustic echo cancellation — headphones, or `--no-barge-in`.
- The shell classifier is a heuristic, not a sandbox. `shell = off` is the only
  hard guarantee.
- Whisper occasionally hallucinates text from silence; the obvious artefacts are
  filtered, but a very noisy room will still produce the odd phantom request.

## License

MIT
