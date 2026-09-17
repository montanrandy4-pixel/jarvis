"""Microphone capture and voice-activity detection.

Everything here imports sounddevice/numpy lazily, so the rest of JARVIS runs on
a machine with no audio stack at all -- you simply cannot start voice mode.
"""

from __future__ import annotations

import collections
import logging
import queue
import time

log = logging.getLogger("jarvis.mic")

SAMPLE_RATE = 16_000  # What both webrtcvad and Whisper want.
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2  # 16-bit mono.


class AudioUnavailable(RuntimeError):
    """Raised when the microphone stack is missing or the device won't open."""


def require_audio():
    """Import the audio dependencies, with an error a human can act on."""
    try:
        import numpy
        import sounddevice
    except ImportError as exc:
        raise AudioUnavailable(
            "Microphone support needs sounddevice and numpy. "
            "Install them with: pip install 'jarvis[voice]'"
        ) from exc
    return sounddevice, numpy


class Microphone:
    """A 16 kHz mono input stream that yields fixed-size frames."""

    def __init__(self, device: str | int | None = None):
        self.device = device or None
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stream = None

    def __enter__(self) -> "Microphone":
        sd, _ = require_audio()

        def callback(indata, frames, time_info, status):
            if status:
                log.debug("input stream status: %s", status)
            self._queue.put(bytes(indata))

        try:
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=FRAME_SAMPLES,
                device=self.device,
                dtype="int16",
                channels=1,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            raise AudioUnavailable(f"Could not open the microphone: {exc}") from exc
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def frames(self, timeout: float = 1.0):
        """Yield raw 30 ms frames as they arrive."""
        while self._stream is not None:
            try:
                yield self._queue.get(timeout=timeout)
            except queue.Empty:
                continue

    def drain(self) -> None:
        """Throw away buffered audio -- e.g. what JARVIS just said to itself."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


class VoiceDetector:
    """Says whether a frame contains speech.

    Uses webrtcvad when it is installed. Otherwise falls back to an energy gate
    that calibrates itself against the room's noise floor -- less precise, but
    it keeps voice mode usable without the extra dependency.
    """

    def __init__(self, aggressiveness: int = 2):
        self._vad = None
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(aggressiveness)
        except ImportError:
            log.info("webrtcvad not installed; using the energy-based detector")
        self._noise_floor: float | None = None
        self._history: collections.deque = collections.deque(maxlen=100)

    @property
    def backend(self) -> str:
        return "webrtcvad" if self._vad else "energy"

    def is_speech(self, frame: bytes) -> bool:
        if len(frame) != FRAME_BYTES:
            return False
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                return False
        return self._energy_is_speech(frame)

    def _energy_is_speech(self, frame: bytes) -> bool:
        _, np = require_audio()
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
        self._history.append(rms)
        if self._noise_floor is None:
            if len(self._history) < 20:
                return False  # Still learning the room.
            self._noise_floor = float(sorted(self._history)[len(self._history) // 4])
        # Speech sits well above the floor; keep an absolute minimum so a silent
        # room with a near-zero floor does not treat hiss as speech.
        threshold = max(self._noise_floor * 3.0, 350.0)
        if rms < self._noise_floor * 1.5:
            # Track slow drift in background noise while nobody is talking.
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
        return rms > threshold


def record_utterance(
    mic: Microphone,
    detector: VoiceDetector,
    *,
    silence_timeout: float = 1.0,
    max_seconds: float = 30.0,
    start_timeout: float = 8.0,
    preroll: bytes = b"",
) -> bytes:
    """Record until the speaker stops talking.

    Returns raw 16-bit PCM, or b"" if nobody said anything. ``preroll`` is audio
    captured before the call (the tail of the wake word) so the first syllable
    of the request is not clipped.
    """
    voiced: list[bytes] = [preroll] if preroll else []
    # A short ring buffer of pre-speech audio, prepended once speech starts.
    lead_in: collections.deque = collections.deque(maxlen=8)
    started = bool(preroll)
    began_at = time.monotonic()
    last_voice = began_at
    silence_frames = 0
    needed_silence = max(1, int(silence_timeout * 1000 / FRAME_MS))

    for frame in mic.frames():
        now = time.monotonic()
        speech = detector.is_speech(frame)

        if not started:
            lead_in.append(frame)
            if speech:
                started = True
                voiced.extend(lead_in)
                voiced.append(frame)
                last_voice = now
            elif now - began_at > start_timeout:
                return b""  # Wake word fired but nothing followed.
            continue

        voiced.append(frame)
        if speech:
            last_voice = now
            silence_frames = 0
        else:
            silence_frames += 1
            if silence_frames >= needed_silence:
                break
        if now - began_at > max_seconds:
            log.info("utterance hit the %.0fs cap", max_seconds)
            break

    audio = b"".join(voiced)
    # Under ~300 ms of audio is a cough or a door, not a request.
    if not started or len(audio) < FRAME_BYTES * 10:
        return b""
    return audio
