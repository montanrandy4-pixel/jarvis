"""Speech synthesis.

Several backends, tried in order of quality: piper (neural, local, best),
pyttsx3 (cross-platform), macOS `say`, espeak-ng, and finally a printer that
writes to the terminal so JARVIS still works on a machine with no audio at all.

:class:`SpeechQueue` is the piece that makes replies feel fast -- it takes text
as it streams from the model, cuts it at sentence boundaries, and speaks each
sentence while the next is still being generated.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import subprocess
import threading

log = logging.getLogger("jarvis.tts")

# End of a sentence: terminator, optional closing quote/bracket, then space/end.
_SENTENCE_END = re.compile(r'([.!?…]["\')\]]?)(\s+|$)')
# Long clause with no terminator in sight -- break at a comma so the pause lands
# somewhere natural instead of mid-phrase.
_SOFT_BREAK = re.compile(r"(,|;|:| -- )\s+")
SOFT_BREAK_AFTER = 160


class Speaker:
    """A synthesis backend."""

    name = "none"
    available = False

    def say(self, text: str) -> None:
        """Speak, blocking until finished or stopped."""
        raise NotImplementedError

    def stop(self) -> None:
        """Cut off whatever is being spoken right now."""


class _SubprocessSpeaker(Speaker):
    """Shared plumbing for backends that shell out to a synthesiser."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _spawn(self, text: str) -> subprocess.Popen:
        raise NotImplementedError

    def say(self, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self._proc = self._spawn(text)
        proc = self._proc
        try:
            proc.wait()
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()


class PiperSpeaker(_SubprocessSpeaker):
    """Neural TTS via the piper binary, piped straight into an audio player."""

    name = "piper"

    def __init__(self, voice: str = ""):
        super().__init__()
        self.binary = shutil.which("piper") or shutil.which("piper-tts")
        self.voice = voice or os.environ.get("PIPER_VOICE", "")
        self.player = _find_player()
        self.available = bool(self.binary and self.voice and self.player)

    def _spawn(self, text: str) -> subprocess.Popen:
        piper = subprocess.Popen(
            [self.binary, "--model", self.voice, "--output_file", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        player = subprocess.Popen(
            self.player,
            stdin=piper.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        piper.stdout.close()  # The player owns the pipe now.
        piper.stdin.write(text.encode())
        piper.stdin.close()
        return player


class SaySpeaker(_SubprocessSpeaker):
    """macOS built-in synthesiser. Genuinely good and always present there."""

    name = "say"

    def __init__(self, voice: str = "", rate: int = 180):
        super().__init__()
        self.binary = shutil.which("say")
        self.voice = voice
        self.rate = rate
        self.available = bool(self.binary)

    def _spawn(self, text: str) -> subprocess.Popen:
        cmd = [self.binary, "-r", str(self.rate)]
        if self.voice:
            cmd += ["-v", self.voice]
        return subprocess.Popen(
            cmd + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


class EspeakSpeaker(_SubprocessSpeaker):
    """Robotic, but installed on nearly every Linux box."""

    name = "espeak"

    def __init__(self, voice: str = "", rate: int = 180):
        super().__init__()
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")
        self.voice = voice
        self.rate = rate
        self.available = bool(self.binary)

    def _spawn(self, text: str) -> subprocess.Popen:
        cmd = [self.binary, "-s", str(self.rate)]
        if self.voice:
            cmd += ["-v", self.voice]
        return subprocess.Popen(
            cmd + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


class Pyttsx3Speaker(Speaker):
    """Cross-platform wrapper over the system voice (SAPI5, NSSpeech, espeak)."""

    name = "pyttsx3"

    def __init__(self, voice: str = "", rate: int = 180):
        self._engine = None
        try:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", rate)
            if voice:
                self._engine.setProperty("voice", voice)
            self.available = True
        except Exception as exc:  # Driver problems are common and not fatal.
            log.debug("pyttsx3 unavailable: %s", exc)
            self.available = False

    def say(self, text: str) -> None:
        if not text.strip() or self._engine is None:
            return
        self._engine.say(text)
        self._engine.runAndWait()

    def stop(self) -> None:
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass


class PrintSpeaker(Speaker):
    """No audio available -- show what would have been said."""

    name = "print"
    available = True

    def say(self, text: str) -> None:
        if text.strip():
            print(f"\N{SPEAKER WITH THREE SOUND WAVES}  {text.strip()}", flush=True)


def _find_player() -> list[str] | None:
    for cmd in (["paplay"], ["aplay", "-q", "-"], ["afplay", "-"], ["play", "-q", "-"]):
        if shutil.which(cmd[0]):
            return cmd if len(cmd) > 1 else cmd + ["-"]
    return None


def make_speaker(config) -> Speaker:
    """Pick a backend: whatever was asked for, else the best one present."""
    want = (config.tts_backend or "auto").lower()
    builders = {
        "piper": lambda: PiperSpeaker(config.voice_name),
        "say": lambda: SaySpeaker(config.voice_name, config.speaking_rate),
        "pyttsx3": lambda: Pyttsx3Speaker(config.voice_name, config.speaking_rate),
        "espeak": lambda: EspeakSpeaker(config.voice_name, config.speaking_rate),
        "none": PrintSpeaker,
    }
    if want in builders:
        speaker = builders[want]()
        if speaker.available:
            return speaker
        log.warning("TTS backend %r is not available; falling back", want)
    for key in ("piper", "say", "pyttsx3", "espeak"):
        speaker = builders[key]()
        if speaker.available:
            return speaker
    return PrintSpeaker()


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Pull complete sentences out of a growing buffer.

    Returns the sentences ready to speak and whatever is left over.
    """
    out: list[str] = []
    rest = buffer
    while True:
        match = _SENTENCE_END.search(rest)
        if match:
            out.append(rest[: match.end(1)].strip())
            rest = rest[match.end() :]
            continue
        if len(rest) > SOFT_BREAK_AFTER:
            soft = _SOFT_BREAK.search(rest, SOFT_BREAK_AFTER // 2)
            if soft:
                out.append(rest[: soft.end(1)].strip())
                rest = rest[soft.end() :]
                continue
        return [s for s in out if s], rest


class SpeechQueue:
    """Speaks streamed text sentence by sentence, on a background thread."""

    def __init__(self, speaker: Speaker):
        self.speaker = speaker
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._buffer = ""
        self._stop = threading.Event()
        # Guards `_pending`, the number of queued-or-playing sentences, which is
        # what `wait` blocks on and what `speaking` reports.
        self._cv = threading.Condition()
        self._pending = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                if not self._stop.is_set():
                    self.speaker.say(item)
            except Exception as exc:
                log.warning("speech failed: %s", exc)
            finally:
                self._settle(1)

    def _settle(self, count: int) -> None:
        with self._cv:
            self._pending = max(0, self._pending - count)
            if self._pending == 0:
                self._cv.notify_all()

    def _enqueue(self, text: str) -> None:
        with self._cv:
            self._pending += 1
        self._queue.put(text)

    def feed(self, chunk: str) -> None:
        """Add streamed text; complete sentences are queued immediately."""
        if self._stop.is_set():
            return
        self._buffer += chunk
        sentences, self._buffer = split_sentences(self._buffer)
        for sentence in sentences:
            self._enqueue(sentence)

    def flush(self) -> None:
        """Queue whatever partial sentence is left at the end of a reply."""
        tail, self._buffer = self._buffer.strip(), ""
        if tail and not self._stop.is_set():
            self._enqueue(tail)

    def say_now(self, text: str) -> None:
        """Speak a single line directly, bypassing the sentence buffer."""
        if text.strip():
            self._enqueue(text)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until everything queued has been spoken."""
        with self._cv:
            return self._cv.wait_for(lambda: self._pending == 0, timeout)

    def interrupt(self) -> None:
        """Barge-in: drop the backlog and silence the current sentence."""
        self._stop.set()
        self._buffer = ""
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        self.speaker.stop()
        self._settle(dropped)

    def resume(self) -> None:
        """Allow speech again after an interruption."""
        self._stop.clear()

    @property
    def speaking(self) -> bool:
        with self._cv:
            return self._pending > 0

    def close(self) -> None:
        self.interrupt()
        self._queue.put(None)
        self._thread.join(timeout=2)
