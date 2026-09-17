"""Waking JARVIS up.

Three ways, in order of preference:

1. openWakeWord, which ships a trained "hey jarvis" model -- runs locally on the
   CPU, low latency, no false starts from ordinary conversation.
2. A transcription fallback: listen for short utterances and check whether the
   wake phrase is in them. Heavier, but needs no extra dependency.
3. Push to talk, for machines where neither works.
"""

from __future__ import annotations

import logging
import re
import time

from .mic import FRAME_BYTES, Microphone, VoiceDetector, record_utterance

log = logging.getLogger("jarvis.wake")


class WakeDetector:
    name = "none"

    def wait(self, mic: Microphone) -> bytes:
        """Block until woken. Returns any trailing audio already captured."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class OpenWakeWordDetector(WakeDetector):
    """Local neural wake-word spotting."""

    name = "openwakeword"

    def __init__(self, phrase: str = "hey jarvis", threshold: float = 0.5):
        from openwakeword.model import Model  # Raises ImportError if absent.

        self.threshold = threshold
        self.key = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
        try:
            self.model = Model(wakeword_models=[self.key])
        except Exception:
            # Unknown phrase: load the bundled models and match by name below.
            log.info("no pretrained model for %r; using the bundled set", phrase)
            self.model = Model()

    def wait(self, mic: Microphone) -> bytes:
        import numpy as np

        self.model.reset()
        for frame in mic.frames():
            if len(frame) != FRAME_BYTES:
                continue
            samples = np.frombuffer(frame, dtype=np.int16)
            scores = self.model.predict(samples)
            for name, score in scores.items():
                if score < self.threshold:
                    continue
                if self.key in name or name in self.key or len(scores) == 1:
                    log.debug("woke on %s (%.2f)", name, score)
                    return b""
        return b""


class TranscriptWakeDetector(WakeDetector):
    """Fallback: transcribe short utterances and look for the phrase."""

    name = "transcript"

    def __init__(self, phrase: str, transcriber, detector: VoiceDetector):
        self.phrase = phrase.lower().strip()
        self.transcriber = transcriber
        self.detector = detector
        # "hey jarvis" should also fire on "hey, Jarvis!" and "hey Jarvis?".
        self._pattern = re.compile(
            r"\b"
            + r"[\s,.!?-]*".join(re.escape(w) for w in self.phrase.split())
            + r"\b",
            re.IGNORECASE,
        )

    def wait(self, mic: Microphone) -> bytes:
        while True:
            audio = record_utterance(
                mic,
                self.detector,
                silence_timeout=0.6,
                max_seconds=6.0,
                start_timeout=3600.0,
            )
            if not audio:
                continue
            try:
                text = self.transcriber.transcribe(audio)
            except Exception as exc:
                log.warning("wake transcription failed: %s", exc)
                time.sleep(0.5)
                continue
            if not text:
                continue
            match = self._pattern.search(text)
            if not match:
                continue
            log.debug("woke on transcript: %r", text)
            # If the request came in the same breath ("hey jarvis, what's the
            # time"), hand the remainder straight back so the user need not
            # repeat themselves.
            remainder = text[match.end() :].strip(" ,.!?")
            return remainder.encode() if remainder else b""


class PushToTalkDetector(WakeDetector):
    """Press Enter to talk. The dependency-free escape hatch."""

    name = "push-to-talk"

    def wait(self, mic: Microphone) -> bytes:
        try:
            input("\n[press Enter to talk, Ctrl-C to quit] ")
        except EOFError:
            raise KeyboardInterrupt from None
        mic.drain()  # Drop the keystroke's worth of stale audio.
        return b""


def make_detector(config, transcriber, detector: VoiceDetector) -> WakeDetector:
    """Best available wake-word backend for this machine."""
    try:
        return OpenWakeWordDetector(config.wake_word)
    except ImportError:
        log.info("openwakeword not installed; waking via transcription")
    except Exception as exc:
        log.warning("wake-word model unavailable (%s); waking via transcription", exc)
    return TranscriptWakeDetector(config.wake_word, transcriber, detector)
