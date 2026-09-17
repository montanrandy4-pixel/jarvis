"""Hands-free voice mode: the wake / listen / think / speak loop."""

from __future__ import annotations

import logging
import threading
import time

from .agent import Agent
from .audio.mic import (
    AudioUnavailable,
    Microphone,
    VoiceDetector,
    record_utterance,
)
from .audio.stt import Transcriber
from .audio.tts import SpeechQueue, make_speaker
from .audio.wake import make_detector
from .tools.timers import SERVICE as TIMERS

log = logging.getLogger("jarvis.voice")

YES = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "do it",
    "please do", "affirmative", "confirm", "confirmed", "proceed", "correct",
}
NO = {
    "no", "nope", "nah", "don't", "do not", "stop", "cancel", "negative",
    "never mind", "forget it", "no thanks",
}
# Things handled locally, without spending a round trip on the API.
SLEEP_WORDS = {"go to sleep", "never mind", "forget it", "that's all", "nothing"}
QUIT_WORDS = {"goodbye", "good bye", "shut down", "power down", "exit", "quit"}
HUSH_WORDS = {"stop", "quiet", "be quiet", "shut up", "enough", "hush"}

# Barge-in needs sustained speech, not one frame -- otherwise JARVIS hears its
# own voice through the speakers and interrupts itself.
BARGE_IN_FRAMES = 10  # ~300 ms.
BARGE_IN_GRACE = 0.7  # Seconds of talking before interruption is allowed.


def normalise(text: str) -> str:
    return text.strip().strip(".,!?").lower()


class VoiceSession:
    """Owns the microphone, the speaker and one Agent, and runs the loop."""

    def __init__(self, config, agent: Agent, *, transcriber=None, speaker=None):
        self.config = config
        self.agent = agent
        self.transcriber = transcriber or Transcriber(
            config.stt_model, config.stt_device
        )
        self.speech = SpeechQueue(speaker or make_speaker(config))
        self.detector = VoiceDetector()
        self.wake = None
        self.mic: Microphone | None = None
        self._watch_stop: threading.Event | None = None
        self._watch_thread: threading.Thread | None = None
        self._cancel: threading.Event | None = None
        self._running = False

    # -- speaking --------------------------------------------------------

    def say(self, text: str) -> None:
        """Speak a line and wait for it to finish."""
        self.speech.resume()
        self.speech.say_now(text)
        self.speech.wait(30)

    def _announce(self, text: str) -> None:
        """Speak without waiting -- used for timers firing mid-conversation."""
        self.speech.resume()
        self.speech.say_now(text)

    # -- listening -------------------------------------------------------

    def _listen(self, *, start_timeout: float = 8.0) -> str:
        """Record one utterance and transcribe it."""
        assert self.mic is not None
        self.mic.drain()  # Discard whatever the speakers leaked into the mic.
        audio = record_utterance(
            self.mic,
            self.detector,
            silence_timeout=self.config.silence_timeout,
            max_seconds=self.config.max_utterance_seconds,
            start_timeout=start_timeout,
        )
        if not audio:
            return ""
        try:
            return self.transcriber.transcribe(audio)
        except Exception as exc:
            log.warning("transcription failed: %s", exc)
            self.say("I couldn't make that out.")
            return ""

    def ask_yes_no(self, question: str) -> bool:
        """Ask aloud and wait for an answer. Used for tool confirmations."""
        was_watching = self._stop_watch()
        try:
            self.say(question)
            for attempt in range(2):
                answer = normalise(self._listen(start_timeout=6.0))
                if not answer:
                    if attempt == 0:
                        self.say("Say yes or no.")
                    continue
                if any(answer.startswith(word) for word in YES):
                    return True
                if any(answer.startswith(word) for word in NO):
                    return False
                if attempt == 0:
                    self.say("Yes or no?")
            self.say("I'll take that as a no.")
            return False
        finally:
            if was_watching:
                self._start_watch(self._cancel)

    # -- barge-in --------------------------------------------------------

    def _start_watch(self, cancel: threading.Event | None) -> None:
        """Listen for the user talking over a reply, and cut it short."""
        if not self.config.barge_in or cancel is None or self.mic is None:
            return
        stop = threading.Event()

        def watch() -> None:
            began = time.monotonic()
            voiced = 0
            for frame in self.mic.frames(timeout=0.2):
                if stop.is_set():
                    return
                if not self.speech.speaking:
                    voiced = 0
                    continue
                if time.monotonic() - began < BARGE_IN_GRACE:
                    continue
                voiced = voiced + 1 if self.detector.is_speech(frame) else 0
                if voiced >= BARGE_IN_FRAMES:
                    log.debug("barge-in detected")
                    cancel.set()
                    self.speech.interrupt()
                    return

        self._watch_stop = stop
        self._watch_thread = threading.Thread(target=watch, daemon=True)
        self._watch_thread.start()

    def _stop_watch(self) -> bool:
        """Stop the barge-in watcher. Returns whether one was running."""
        if self._watch_thread is None:
            return False
        self._watch_stop.set()
        self._watch_thread.join(timeout=1.0)
        self._watch_thread = None
        self._watch_stop = None
        return True

    # -- the loop --------------------------------------------------------

    def _announce_timer(self, timer) -> None:
        label = f" for {timer.label}" if timer.label else ""
        self._announce(f"Your timer{label} is up.")

    def _handle_local(self, said: str) -> str | None:
        """Deal with the utterances that need no model call.

        Returns "sleep", "quit", "handled", or None to pass it to Claude.
        """
        text = normalise(said)
        if text in HUSH_WORDS:
            self.speech.interrupt()
            return "handled"
        if text in QUIT_WORDS:
            return "quit"
        if text in SLEEP_WORDS:
            return "sleep"
        return None

    def _respond(self, said: str) -> None:
        """One exchange: stream the reply into the speaker as it arrives."""
        self._cancel = threading.Event()
        self.speech.resume()
        self._start_watch(self._cancel)
        try:
            turn = self.agent.reply(
                said, on_text=self.speech.feed, cancel=self._cancel
            )
        finally:
            self._stop_watch()

        if self._cancel.is_set():
            self.speech.interrupt()
            return  # The user cut in; go straight back to listening.

        self.speech.flush()
        message = turn.refusal or turn.error
        if message and not turn.text:
            self.speech.say_now(message)
        elif message:
            self.speech.say_now(f"One thing: {message}")
        self.speech.wait(120)
        self._cancel = None

    def run(self) -> None:
        """Start listening. Blocks until Ctrl-C or a spoken goodbye."""
        self.transcriber.load()
        TIMERS.on_fire = self._announce_timer
        self._running = True

        with Microphone(self.config.input_device or None) as mic:
            self.mic = mic
            self.wake = make_detector(self.config, self.transcriber, self.detector)
            log.info(
                "voice mode: wake=%s stt=%s tts=%s vad=%s",
                self.wake.name,
                self.transcriber.model_name,
                self.speech.speaker.name,
                self.detector.backend,
            )
            print(
                f'Listening. Say "{self.config.wake_word}" to wake me.',
                flush=True,
            )
            self.say(_greeting(self.config))

            follow_up = False
            while self._running:
                try:
                    said = self._next_utterance(follow_up)
                except KeyboardInterrupt:
                    break
                if not said:
                    follow_up = False
                    continue

                print(f"> {said}", flush=True)
                action = self._handle_local(said)
                if action == "quit":
                    self.say("Goodbye, sir.")
                    break
                if action == "sleep":
                    follow_up = False
                    continue
                if action == "handled":
                    follow_up = True
                    continue

                self._respond(said)
                # Stay awake briefly so a follow-up needs no wake word.
                follow_up = self.config.followup_window > 0

        self.close()

    def _next_utterance(self, follow_up: bool) -> str:
        """Either continue the conversation or wait to be woken."""
        if follow_up:
            said = self._listen(start_timeout=self.config.followup_window)
            if said:
                return said
            return ""  # Window elapsed; fall back to the wake word next round.
        trailing = self.wake.wait(self.mic)
        if trailing:
            # The wake detector already heard the request in the same breath.
            return trailing.decode(errors="ignore")
        return self._listen(start_timeout=6.0)

    def close(self) -> None:
        self._running = False
        self._stop_watch()
        TIMERS.on_fire = None
        TIMERS.cancel_all()
        self.speech.close()
        if self.mic is not None:
            self.mic.close()
            self.mic = None


def _greeting(config) -> str:
    hour = time.localtime().tm_hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    who = f", {config.address_user_as}" if config.address_user_as else ""
    return f"Good {part}{who}. I'm listening."


def run_voice(config, agent_factory) -> int:
    """Entry point used by the CLI. Returns a process exit code."""
    session_holder: dict[str, VoiceSession] = {}

    def confirm(_name: str, summary: str) -> bool:
        session = session_holder.get("session")
        if session is None:
            return False
        return session.ask_yes_no(f"Shall I {summary}?")

    agent = agent_factory(confirm)
    session = VoiceSession(config, agent)
    session_holder["session"] = session
    try:
        session.run()
    except AudioUnavailable as exc:
        print(f"\n{exc}\n\nYou can still talk to JARVIS with: jarvis chat")
        return 1
    except KeyboardInterrupt:
        print()
    finally:
        session.close()
        path = agent.save_transcript()
        if path:
            log.info("transcript saved to %s", path)
    return 0
