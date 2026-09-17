"""Speech to text, local and offline via faster-whisper.

Nothing leaves the machine: audio is transcribed on the CPU (or GPU if one is
configured) and only the resulting text is ever sent to the API.
"""

from __future__ import annotations

import logging

from .mic import SAMPLE_RATE, require_audio

log = logging.getLogger("jarvis.stt")

# Whisper likes to fill silence with its training data's boilerplate. These are
# the usual offenders; dropping them avoids acting on a hallucinated request.
HALLUCINATIONS = {
    "thank you.",
    "thanks for watching!",
    "thank you for watching.",
    "you",
    "bye.",
    ".",
    "subtitles by the amara.org community",
    "[music]",
    "[blank_audio]",
}


class TranscriptionUnavailable(RuntimeError):
    pass


class Transcriber:
    """Wraps a faster-whisper model, loaded once and reused."""

    def __init__(self, model: str = "base.en", device: str = "auto"):
        self.model_name = model
        self.device = device
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionUnavailable(
                "Speech recognition needs faster-whisper. "
                "Install it with: pip install 'jarvis[voice]'"
            ) from exc
        device = self.device if self.device != "auto" else "auto"
        # int8 keeps a CPU-only machine responsive; float16 is used on GPU.
        compute = "int8" if device in {"auto", "cpu"} else "float16"
        log.info("loading whisper model %s (%s)", self.model_name, compute)
        self._model = WhisperModel(
            self.model_name, device=device, compute_type=compute
        )

    def transcribe(self, pcm: bytes) -> str:
        """Turn 16 kHz mono PCM into text. Returns "" for silence or noise."""
        if not pcm:
            return ""
        self.load()
        _, np = require_audio()
        # Whisper wants float32 in [-1, 1].
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio,
            language="en" if self.model_name.endswith(".en") else None,
            beam_size=1,  # Greedy: this is a latency-sensitive path.
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return "" if is_noise(text) else text


def is_noise(text: str) -> bool:
    """Filter out empty results and Whisper's silence hallucinations."""
    cleaned = text.strip().lower()
    if len(cleaned) < 2:
        return True
    return cleaned.strip("!.,") in {h.strip("!.,") for h in HALLUCINATIONS}


def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / SAMPLE_RATE
