#!/bin/sh
# Installs JARVIS on macOS or Linux, start to finish:
#   the local AI engine (Ollama) and a model, JARVIS itself in its own
#   environment, a launcher in your applications, and optionally start-at-login.
#
#   ./install.sh                  from a downloaded copy of the repository
#   curl -fsSL https://raw.githubusercontent.com/montanrandy4-pixel/jarvis/HEAD/install.sh | sh
#
# Options: --yes (accept every default), --autostart / --no-autostart,
# --model NAME. Environment: JARVIS_MODEL, JARVIS_HOME (default ~/.jarvis),
# JARVIS_SOURCE (what pip installs; default this checkout or GitHub).
# Safe to run again: it updates what is already there.

set -eu

REPO="https://github.com/montanrandy4-pixel/jarvis"
MODEL="${JARVIS_MODEL:-llama3.1:8b}"
JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
ASSUME_YES=0
AUTOSTART=ask

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --autostart) AUTOSTART=yes ;;
    --no-autostart) AUTOSTART=no ;;
    --model) shift; MODEL="$1" ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -t 1 ]; then BOLD=$(printf '\033[1m'); CYAN=$(printf '\033[36m'); RED=$(printf '\033[31m'); RESET=$(printf '\033[0m')
else BOLD=""; CYAN=""; RED=""; RESET=""; fi
step() { printf '\n%s▸ %s%s\n' "$CYAN$BOLD" "$1" "$RESET"; }
say() { printf '  %s\n' "$1"; }
fail() { printf '\n%s✗ %s%s\n' "$RED$BOLD" "$1" "$RESET" >&2; exit 1; }

# Ask a yes/no question. Reads the terminal even when this script arrives on a
# pipe from curl, and takes the default when there is no terminal at all.
ask() {
  question="$1"; default="$2"
  if [ "$ASSUME_YES" = 1 ] || ! [ -r /dev/tty ]; then [ "$default" = y ]; return; fi
  if [ "$default" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
  printf '  %s %s ' "$question" "$hint" > /dev/tty
  read -r answer < /dev/tty || answer=""
  case "${answer:-$default}" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

OS=$(uname -s)
case "$OS" in
  Darwin) PLATFORM=mac ;;
  Linux) PLATFORM=linux ;;
  *) fail "This installer is for macOS and Linux. On Windows, double-click install.cmd." ;;
esac

printf '%s\n' "${CYAN}${BOLD}J A R V I S${RESET}  installer"

# --- 1. Python ---------------------------------------------------------------
step "Checking for Python 3.10 or newer"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PYTHON=$(command -v "$candidate"); break
  fi
done
if [ -z "$PYTHON" ]; then
  if [ "$PLATFORM" = mac ] && command -v brew >/dev/null 2>&1 &&
     ask "Python 3.10+ is needed. Install it with Homebrew?" y; then
    brew install python@3.12
    PYTHON="$(brew --prefix)/bin/python3.12"
  elif [ "$PLATFORM" = mac ]; then
    fail "Install Python from https://www.python.org/downloads/ and run this again."
  else
    fail "Install Python 3.10+ first, for example:
    sudo apt install python3 python3-venv      (Ubuntu, Debian)
    sudo dnf install python3                   (Fedora)"
  fi
fi
say "using $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

# --- 2. Ollama, the engine that runs the model on this machine ---------------
step "Checking for Ollama (runs the AI model on this computer)"
ollama_up() { curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; }

if ! command -v ollama >/dev/null 2>&1; then
  ask "Ollama is not installed. Install it now?" y ||
    fail "JARVIS needs Ollama. Get it from https://ollama.com/download and run this again."
  if [ "$PLATFORM" = mac ]; then
    if command -v brew >/dev/null 2>&1; then
      brew install ollama
      brew services start ollama >/dev/null 2>&1 || true
    else
      fail "Download Ollama from https://ollama.com/download, open it once, then run this again."
    fi
  else
    curl -fsSL https://ollama.com/install.sh | sh
  fi
fi
if ! ollama_up; then
  say "starting the Ollama server"
  (nohup ollama serve >/dev/null 2>&1 &)
  tries=0
  until ollama_up; do
    tries=$((tries + 1))
    [ $tries -gt 30 ] && fail "Ollama did not start. Try running 'ollama serve' yourself, then run this again."
    sleep 1
  done
fi
say "Ollama is running"

# --- 3. The model --------------------------------------------------------------
step "Downloading the model $MODEL (about 5 GB the first time)"
if ollama list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -qx "$MODEL"; then
  say "already downloaded"
else
  ollama pull "$MODEL"
fi

# --- 4. JARVIS -------------------------------------------------------------------
step "Installing JARVIS into $JARVIS_HOME"
HERE=""
case "$0" in */*) HERE=$(cd "$(dirname "$0")" && pwd) ;; esac
if [ -n "${JARVIS_SOURCE:-}" ]; then
  SOURCE="$JARVIS_SOURCE"
elif [ -n "$HERE" ] && [ -f "$HERE/pyproject.toml" ] && grep -q '^name = "jarvis"' "$HERE/pyproject.toml"; then
  SOURCE="$HERE"
elif command -v git >/dev/null 2>&1; then
  SOURCE="git+$REPO"
else
  SOURCE="$REPO/archive/HEAD.tar.gz"
fi
say "from $SOURCE"

mkdir -p "$JARVIS_HOME"
if ! [ -x "$JARVIS_HOME/venv/bin/python" ]; then
  "$PYTHON" -m venv "$JARVIS_HOME/venv" 2>/dev/null ||
    fail "Could not create a Python environment. On Ubuntu or Debian: sudo apt install python3-venv"
fi
"$JARVIS_HOME/venv/bin/python" -m pip install --quiet --upgrade pip
# Force the reinstall: pip would otherwise keep an older copy of the same version.
"$JARVIS_HOME/venv/bin/python" -m pip install --quiet --upgrade --force-reinstall "$SOURCE"

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
ln -sf "$JARVIS_HOME/venv/bin/jarvis" "$BIN/jarvis"
say "the 'jarvis' command is in $BIN"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) say "(add $BIN to your PATH to type 'jarvis' in a terminal; the launcher works either way)" ;;
esac

# --- 5. Launcher -----------------------------------------------------------------
step "Adding JARVIS to your applications"
SETUP_FLAGS=""
if [ "$AUTOSTART" = yes ] ||
   { [ "$AUTOSTART" = ask ] && ask "Start JARVIS in the background when you log in? (opens instantly)" y; }; then
  SETUP_FLAGS="--autostart"
fi
# shellcheck disable=SC2086
"$JARVIS_HOME/venv/bin/jarvis" setup $SETUP_FLAGS

# --- 6. Check and open ----------------------------------------------------------
step "Checking everything works"
"$JARVIS_HOME/venv/bin/jarvis" --model "$MODEL" doctor || true

if [ "$MODEL" != "llama3.1:8b" ]; then
  mkdir -p "$HOME/.config/jarvis"
  if ! [ -f "$HOME/.config/jarvis/config.toml" ]; then
    printf '[jarvis]\nmodel = "%s"\n' "$MODEL" > "$HOME/.config/jarvis/config.toml"
    say "saved model = $MODEL in ~/.config/jarvis/config.toml"
  fi
fi

step "Opening JARVIS"
(nohup "$JARVIS_HOME/venv/bin/jarvis" app >/dev/null 2>&1 &)
printf '\n%sJARVIS is installed.%s ' "$BOLD" "$RESET"
if [ "$PLATFORM" = mac ]; then
  echo "Open it any time from Spotlight: press Cmd+Space and type JARVIS."
else
  echo "Open it any time from your applications menu, or type 'jarvis'."
fi
