"""The voice loop's decisions: local commands, confirmations, barge-in."""

from __future__ import annotations

import threading

import pytest
from fakes import FakeBackend, says

from jarvis.agent import Agent
from jarvis.audio.tts import PrintSpeaker
from jarvis.tools import Registry
from jarvis.voice import VoiceSession, normalise


class RecordingSpeaker(PrintSpeaker):
    def __init__(self):
        self.said: list[str] = []
        self.stopped = 0

    def say(self, text: str) -> None:
        self.said.append(text)

    def stop(self) -> None:
        self.stopped += 1


@pytest.fixture
def session(config, memory, monkeypatch):
    agent = Agent(
        config, Registry(), memory, backend=FakeBackend(script=[says("Very good.")])
    )
    speaker = RecordingSpeaker()
    session = VoiceSession(config, agent, transcriber=object(), speaker=speaker)
    session.speaker = speaker
    yield session
    session.speech.close()


def heard(session, *utterances):
    """Script what the microphone will 'hear' on successive listens."""
    queue = list(utterances)
    session._listen = lambda **kwargs: queue.pop(0) if queue else ""
    return session


class TestLocalCommands:
    @pytest.mark.parametrize("said", ["goodbye", "Shut down.", "quit"])
    def test_farewells_end_the_session(self, session, said):
        assert session._handle_local(said) == "quit"

    @pytest.mark.parametrize("said", ["never mind", "go to sleep", "That's all."])
    def test_dismissals_go_back_to_sleep(self, session, said):
        assert session._handle_local(said) == "sleep"

    def test_hush_silences_without_calling_the_model(self, session):
        session.speech.say_now("a long explanation")

        assert session._handle_local("stop") == "handled"
        assert session.speaker.stopped >= 1

    @pytest.mark.parametrize(
        "said", ["what time is it", "set a timer", "stop the deployment"]
    )
    def test_real_requests_are_passed_to_the_model(self, session, said):
        assert session._handle_local(said) is None

    def test_normalise_strips_punctuation_and_case(self):
        assert normalise("  Go To Sleep.  ") == "go to sleep"


class TestSpokenConfirmation:
    @pytest.mark.parametrize("answer", ["yes", "Yeah.", "go ahead", "do it", "sure"])
    def test_affirmatives_allow_the_action(self, session, answer):
        assert heard(session, answer).ask_yes_no("Shall I?") is True

    @pytest.mark.parametrize("answer", ["no", "Nope.", "cancel", "never mind"])
    def test_negatives_block_it(self, session, answer):
        assert heard(session, answer).ask_yes_no("Shall I?") is False

    def test_an_unclear_answer_is_asked_again(self, session):
        heard(session, "perhaps later", "yes")

        assert session.ask_yes_no("Shall I?") is True
        assert "Yes or no?" in session.speaker.said

    def test_silence_is_treated_as_no(self, session):
        heard(session, "", "")

        assert session.ask_yes_no("Shall I?") is False
        assert "I'll take that as a no." in session.speaker.said


class TestReplying:
    def test_a_reply_is_spoken_sentence_by_sentence(self, session):
        session.agent.backend.script = [
            says("The build passed. Nothing needs your attention.")
        ]

        session._respond("how did the build go")
        session.speech.wait(5)

        assert session.speaker.said == [
            "The build passed.",
            "Nothing needs your attention.",
        ]

    def test_an_interrupted_reply_is_cut_off(self, session, monkeypatch):
        def interrupted(text, on_text=None, cancel=None):
            on_text("I was going to say something long. ")
            cancel.set()  # The user started talking.
            from jarvis.agent import Turn

            return Turn(text="", stop_reason="cancelled")

        monkeypatch.setattr(session.agent, "reply", interrupted)

        session._respond("stop talking")

        assert session.speaker.stopped >= 1

    def test_errors_are_spoken_rather_than_swallowed(self, session):
        from jarvis.agent import Turn

        session.agent.reply = lambda *a, **k: Turn(error="I can't reach the network.")

        session._respond("what's the weather")
        session.speech.wait(5)

        assert "I can't reach the network." in session.speaker.said


class TestFollowUps:
    def test_a_follow_up_skips_the_wake_word(self, session):
        heard(session, "and what about tomorrow")
        session.wake = None  # Must not be consulted.

        assert session._next_utterance(follow_up=True) == "and what about tomorrow"

    def test_an_empty_follow_up_window_returns_to_sleep(self, session):
        heard(session, "")

        assert session._next_utterance(follow_up=True) == ""

    def test_a_request_in_the_same_breath_is_not_repeated(self, session):
        session.wake = type(
            "W", (), {"wait": lambda self, mic: b"what time is it"}
        )()

        assert session._next_utterance(follow_up=False) == "what time is it"


def test_barge_in_is_skipped_when_disabled(session):
    session.config.barge_in = False

    session._start_watch(threading.Event())

    assert session._watch_thread is None
