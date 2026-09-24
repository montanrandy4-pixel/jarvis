# JARVIS

A voice assistant in the style of Iron Man's, running on your own machine.
Open it, talk to it, and it answers out loud, using your computer when the
answer needs checking rather than guessing.

No API key. No cloud model. Nothing to sign up for.

![the app](docs/screenshot.png)

## Install

One step. The installer sets up the AI engine and model, JARVIS itself, and a
launcher, then opens JARVIS. Most of the time goes on the model download,
about 5 GB.

**Windows.** Download this repository (*Code → Download ZIP*), unzip it, and
double-click **`install.cmd`**.

**macOS or Linux.** From the downloaded folder:

```bash
./install.sh
```

or without downloading anything first:

```bash
curl -fsSL https://raw.githubusercontent.com/montanrandy4-pixel/jarvis/HEAD/install.sh | sh
```

It asks two things: whether to install Ollama (the engine that runs the model
on your computer) if you don't have it, and whether JARVIS should start in the
background when you log in, so it opens instantly. Run it again at any time to
update. `--yes` accepts every default; `--model qwen2.5:7b` picks another model.

## Opening it

| | |
|---|---|
| Windows | **Ctrl+Alt+J** from anywhere (**Ctrl+Alt+K** starts a call), or JARVIS in the Start menu or on the desktop |
| macOS | **Cmd+Space**, type JARVIS; or Launchpad, or drag it to the Dock |
| Linux | JARVIS in the applications menu, or `jarvis` |
| Any browser | http://127.0.0.1:8765 |

JARVIS opens in its own window (Chrome or Edge app mode, when either is
installed). Opening it again while it is running brings up the same one. Chrome
and Edge also show **Install app** in the top bar, which pins it to the taskbar
or Dock like any other program.

To stop it: *Settings → Shut down JARVIS*, or `jarvis stop`.

## Calling JARVIS

Tap the reactor, or **Call**, and you are on a call: talk normally, and it
answers out loud and goes straight back to listening. No button to hold, no
wake word between sentences.

- **Cut in** by talking over it, as you would with a person. It stops and
  listens. Its own voice coming back through your speakers is recognised by
  comparing what the microphone hears with what it just said, so it does not
  interrupt itself. Headphones make this foolproof.
- **Pause mid-sentence** and carry on: if it has not started answering, the
  two halves are answered as one question.
- **Answer permission questions out loud.** When it asks "Shall I delete
  that?", say yes or no.
- **Hang up** by saying "bye" or "that's all", with *End call*, or with Esc.
  *Mute* stops it hearing you without ending the call.

On a call it is told to answer in a sentence or two and to ask one short
question when it needs something, as a person on the phone would. `jarvis call`,
Ctrl+Alt+K on Windows, and *Start a call* on the Linux launcher open JARVIS
straight into a call. Calls need Chrome or Edge, which provide the speech
recognition.

## On your phone, with nothing to install

[`hosted/jarvis.html`](hosted/jarvis.html) is the same HUD as a page on
claude.ai. Open its link on any phone, tablet or computer where you are signed
in to claude.ai. Claude is the brain, on your own claude.ai account, so there
is still no API key, and nothing runs on your computer.

- **Talk** with your device's dictation: the microphone key on a phone
  keyboard, Windows + H, or Fn twice on a Mac. claude.ai pages cannot use the
  microphone directly. Replies are read aloud.
- **Memory follows you.** Facts it remembers are stored privately under your
  account and appear on every device you open it on.
- **Timers** ring on the device that set them.
- **Fast or Thorough** in Settings picks a quick model for conversation or a
  slower one that thinks first.

It cannot use your computer (no shell or files) and cannot hold a hands-free
call, because claude.ai pages are never given the microphone. Those are what
the desktop app is for. The first message asks you to allow the page to use
Claude. To change the page, edit the file and republish it as a claude.ai
artifact.

## What it is

A local web app with a Python backend. Type or talk; replies stream back and
are spoken as they arrive. The model runs in Ollama alongside it, so the whole
thing works on a plane.

| Piece | What it does |
|---|---|
| The HUD at `127.0.0.1:8765` | Arc reactor that reacts to your voice and its own, live system gauges, timers, memory, conversation log |
| Model backend | Ollama by default; any OpenAI-compatible server instead |
| Tools | Shell, files, timers, memory, system status, web search |
| Memory | Durable facts in a JSON file you can read and edit |

The reactor shows what JARVIS is doing: cyan and steady when standing by,
rippling with your voice while it listens, gold and spinning while it thinks,
pulsing as it speaks, red when something needs attention.

## Installing by hand

```bash
# 1. A model that can call tools
curl -fsSL https://ollama.com/install.sh | sh     # if you don't have it
ollama pull llama3.1:8b                           # or qwen2.5:7b, mistral-nemo

# 2. JARVIS
git clone https://github.com/montanrandy4-pixel/jarvis && cd jarvis
pip install -e .

# 3. Check, add the launcher, and run
jarvis doctor
jarvis setup --autostart      # Start menu / Spotlight / app menu entry
jarvis
```

`jarvis doctor` verifies the model server is up, the model is pulled, and that
it supports tool calling. It names the exact command to fix whatever is
missing.

### Using a different server

```bash
jarvis --backend openai --base-url http://localhost:8080/v1 --model local-model
jarvis --backend openai --base-url https://api.groq.com/openai/v1 --model llama-3.3-70b
```

Anything speaking the OpenAI chat-completions protocol works: llama.cpp, vLLM,
LM Studio, text-generation-webui, or a hosted service. Set the key in the
environment variable named by `api_key_env` (default `OPENAI_API_KEY`).

## Commands

| Command | What it does |
|---|---|
| `jarvis` / `jarvis app` | Open the app |
| `jarvis call` | Open the app on a hands-free call |
| `jarvis chat` | Talk to it in the terminal |
| `jarvis ask "..."` | One question, answer to stdout |
| `jarvis listen` | Hands-free voice in the terminal, no browser |
| `jarvis memory` | Show, add or delete what it remembers |
| `jarvis doctor` | Check the setup |
| `jarvis setup` | Add the launcher; `--autostart` to start at login, `--remove` to undo |
| `jarvis stop` | Stop the running app |

Flags work before or after the subcommand: `--backend`, `--model`, `--base-url`,
`--shell off|confirm|on`, `--no-web`, `--port`, `--no-browser`, `--debug`.
`JARVIS_APP_WINDOW=false` opens a normal browser tab instead of an app window.

## Talking to it

The app uses your browser's speech engines, so voice needs no extra install.

- **Tap the reactor** to start a call (above). The microphone button next to
  the text box takes a single sentence instead.
- **Hands-free**: turn on *Listen for "hey jarvis"* in Settings, and it waits for
  the wake word. Say "hey jarvis, what's on my disk" in one breath and it skips
  straight to the question.
- **Replies are spoken as they stream**, sentence by sentence, so it starts
  talking before it has finished thinking.
- **Reasoning is hidden.** Models that emit `<think>` blocks have them stripped
  before anything is shown or spoken.

Speech recognition in Chrome and Edge goes through the browser's own online
service. If you want speech to stay local too, `jarvis listen` runs the wake
word and Whisper on your machine instead — `pip install -e '.[voice]'`.

## Tools

| Tool | Notes |
|---|---|
| `run_shell` | Gated by the `shell` setting (below) |
| `read_file`, `write_file`, `list_directory` | Confined to the workspace roots |
| `web_search`, `fetch_url` | DuckDuckGo, no API key |
| `remember`, `recall`, `forget` | Durable facts |
| `set_timer`, `list_timers`, `cancel_timer` | Announced out loud when they fire |
| `system_status` | Uptime, load, memory, disk, battery |

### Safety

JARVIS can run commands on your machine, so two gates are on by default:

- **`shell = confirm`** — read-only commands (`ls`, `df`, `git status`) run
  freely; anything that might change something shows a permission card with the
  exact command and waits for you. The classifier errs toward asking: unknown
  commands, pipes, redirects and substitutions all count as writes.
  `shell = off` removes the tool entirely; `shell = on` stops asking.
- **Workspace confinement** — file tools only touch `workspace` (your home
  directory by default), resolved before the check so `../..` cannot climb out.

- **Other websites cannot drive it.** Any page you visit can make your browser
  send requests to `127.0.0.1`; JARVIS refuses those whose Origin is not its own,
  and requests under a hostname other than a loopback one (DNS rebinding).

The app binds to `127.0.0.1` and holds one conversation. Memory, transcripts and
settings live in `~/.local/state/jarvis`.

## Configuration

`JARVIS_*` environment variables or `~/.config/jarvis/config.toml`:

```toml
[jarvis]
backend = "ollama"
model = "llama3.1:8b"
temperature = 0.6
context_tokens = 8192
shell = "confirm"
workspace = ["~/projects", "~/Documents"]
address_user_as = "sir"
port = 8765
```

See `.env.example` for the full list.

### Choosing a model

It must support tool calling — `jarvis doctor` checks and says so. `llama3.1:8b`
is a good default; `qwen2.5:7b` is faster on modest hardware; a 3B model is
quick but will misuse tools. Bigger models answer better and speak later: for a
voice assistant, latency is most of the experience.

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
against the schema before the handler runs — small models emit malformed
arguments often enough that this matters — and a handler that raises becomes an
error the model can recover from rather than a crash.

## Development

```bash
pip install -e '.[dev]'
pytest                    # 333 tests, no model or microphone required
```

The agent loop runs against a scripted fake backend; the Ollama and
OpenAI-compatible clients run against a stub HTTP server that replays real
protocol traffic; the app's endpoints are tested over real HTTP, including the
permission round trip. Launchers for all three OSes are built into a temporary
home directory and inspected; `install.sh` has been run end to end on Linux.
The Windows and macOS installers have not been run on those systems yet.

### Layout

```
src/jarvis/
  server.py     the app: HTTP, SSE streaming, permission prompts, telemetry
  web/          the HUD (no build step, no CDN); reactor.js draws the reactor
  launcher.py   single instance, app window, stop
  shortcuts.py  `jarvis setup`: Start menu, Spotlight and app-menu launchers
hosted/
  jarvis.html   the claude.ai version: same HUD, Claude as the brain
  agent.py      conversation loop, tool execution, history trimming
  backends/     ollama + openai-compatible clients, built on urllib
  persona.py    system prompt
  memory.py     durable facts
  tools/        registry, validation, and the built-in tools
  voice.py      terminal voice mode (local wake word + whisper)
  audio/        mic, VAD, whisper, speech synthesis
```

## Running a shop

The repository also contains a Shopify autopilot that uses the same local
model: it turns a supplier feed into listings, keeps prices and stock in step,
and triages orders. When it is configured, JARVIS gains read-only tools for it,
so you can ask how the shop is doing out loud.

```bash
shop init && shop doctor && shop plan
```

See [SHOP.md](SHOP.md) — including what it deliberately will not do.

## Limitations

- Small local models call tools less reliably than frontier ones. If it ignores
  a tool, try `llama3.1:8b` or a larger model before assuming a bug.
- Browser speech recognition is not local (see above), and Safari and Firefox
  support it poorly. Typing always works.
- Web search scrapes DuckDuckGo's HTML, which can break without warning; it
  fails as a tool error rather than a crash.
- The shell classifier is a heuristic, not a sandbox. `shell = off` is the only
  hard guarantee.

## License

MIT
