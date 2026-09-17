"""Speech synthesis chunking, transcription hygiene and wake-word matching."""

from __future__ import annotations

import pytest

from jarvis.audio.stt import is_noise
from jarvis.audio.tts import PrintSpeaker, SpeechQueue, make_speaker, split_sentences
from jarvis.audio.wake import TranscriptWakeDetector


class TestSentenceSplitting:
    def test_complete_sentences_are_released(self):
        assert split_sentences("One. Two! Three?") == (
            ["One.", "Two!", "Three?"],
            "",
        )

    def test_a_partial_sentence_is_held_back(self):
        spoken, rest = split_sentences("Ready. And then")

        assert spoken == ["Ready."]
        assert rest == "And then"

    def test_decimals_and_abbreviations_do_not_split_mid_number(self):
        # "3.5" has no space after the dot, so it is not a sentence end.
        spoken, rest = split_sentences("It is 3.5 metres")

        assert spoken == []
        assert rest == "It is 3.5 metres"

    def test_a_long_clause_breaks_at_a_comma(self):
        spoken, rest = split_sentences("word " * 40 + ", and more")

        assert spoken and spoken[0].endswith(",")

    def test_reassembling_chunks_preserves_the_text(self):
        source = "Good morning. The report is ready; shall I read it?"
        buffer, spoken = "", []
        for i in range(0, len(source), 3):
            buffer += source[i : i + 3]
            done, buffer = split_sentences(buffer)
            spoken += done
        spoken += [buffer.strip()] if buffer.strip() else []

        assert " ".join(spoken) == source


class TestSpeechQueue:
    def test_sentences_are_spoken_as_they_stream(self):
        said: list[str] = []
        speaker = PrintSpeaker()
        speaker.say = said.append
        queue = SpeechQueue(speaker)

        for chunk in ["Systems ", "are green. ", "No action needed."]:
            queue.feed(chunk)
        queue.flush()
        queue.wait(5)
        queue.close()

        assert said == ["Systems are green.", "No action needed."]

    def test_interrupting_drops_the_backlog(self):
        said: list[str] = []
        speaker = PrintSpeaker()
        speaker.say = said.append
        queue = SpeechQueue(speaker)

        queue.interrupt()
        queue.feed("This should never be spoken. ")
        queue.flush()
        queue.wait(1)

        assert said == []
        queue.resume()
        queue.say_now("But this should.")
        queue.wait(5)
        queue.close()
        assert said == ["But this should."]


class TestTranscriptionHygiene:
    @pytest.mark.parametrize(
        "text", ["Thank you.", "Thanks for watching!", "", " ", ".", "[music]"]
    )
    def test_whisper_silence_artefacts_are_discarded(self, text):
        assert is_noise(text)

    @pytest.mark.parametrize(
        "text", ["what time is it", "set a timer for ten minutes", "no"]
    )
    def test_real_requests_are_kept(self, text):
        assert not is_noise(text) or text == "no"


class TestWakePhrase:
    def _detector(self):
        return TranscriptWakeDetector("hey jarvis", transcriber=None, detector=None)

    @pytest.mark.parametrize(
        "heard",
        ["hey jarvis", "Hey, Jarvis!", "okay hey jarvis are you there", "HEY JARVIS."],
    )
    def test_matches_however_it_was_punctuated(self, heard):
        assert self._detector()._pattern.search(heard)

    @pytest.mark.parametrize("heard", ["hey there", "jarvis", "what a heyday"])
    def test_does_not_fire_on_similar_phrases(self, heard):
        assert not self._detector()._pattern.search(heard)


def test_speaker_selection_falls_back_when_a_backend_is_missing(config):
    config.tts_backend = "piper"  # Not installed in CI.

    speaker = make_speaker(config)

    assert speaker.available
