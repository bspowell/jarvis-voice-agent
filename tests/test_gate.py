"""The wake-word gate: when Flux listens, and which of its words reach LiveKit.

The spotter and Flux are fakes (fakes.py) and the gate's waits last milliseconds, so each state
(asleep → awake → follow-up → asleep, and the echo guard while he speaks) runs offline.
"""

import asyncio

import pytest
from fakes import FRAME, FakeEars, FakeSpotter, end, final, frame, interim, start
from livekit import rtc
from livekit.agents import stt

from jarvis.voice.gate import (
    Gate,
    Timing,
    cuts_in,
    only_cut_in_words,
    only_wake_words,
    sounds_like,
)
from jarvis.voice.spotter import STOP, WAKE

FAST = Timing(pre_roll=4 * FRAME, after_wake=0.1, follow_up=0.05, question=0.15)
Kind = stt.SpeechEventType


def heard(events: list[stt.SpeechEvent]) -> list[str]:
    """Events, readably: ["start", "interim: Stop", "final: Stop.", "end"]."""
    names = {Kind.START_OF_SPEECH: "start", Kind.END_OF_SPEECH: "end"}
    return [
        names.get(event.type)
        or f"{event.type.value.removesuffix('_transcript')}: {event.alternatives[0].text}"
        for event in events
        if event.type is not Kind.RECOGNITION_USAGE
    ]


async def settle() -> None:
    """Let the gate's tasks catch up (they wait on each other, not on time)."""
    for _ in range(10):
        await asyncio.sleep(0)


class Session:
    """A voice session around the gate: your mic, Flux (fake), and what LiveKit gets."""

    def __init__(self) -> None:
        self.flux: list[FakeEars] = []  # every Flux stream opened, in order
        self.interrupted = 0
        self.question = False  # is "Shall I…?" waiting for an answer?
        self.he_said = ""
        self.gate = Gate(
            self.open_ears,
            interrupt=self.interrupt,
            question_open=lambda: self.question,
            his_words=lambda: self.he_said,
            timing=FAST,
        )
        self.mic: asyncio.Queue[rtc.AudioFrame | None] = asyncio.Queue()
        self.livekit: list[stt.SpeechEvent] = []  # what LiveKit was given

    def open_ears(self) -> FakeEars:
        self.flux.append(FakeEars())
        return self.flux[-1]

    def interrupt(self) -> None:
        self.interrupted += 1

    @property
    def ears(self) -> FakeEars:
        return self.flux[-1]

    async def say(self, *frames: rtc.AudioFrame) -> None:
        for mic_frame in frames:
            self.mic.put_nowait(mic_frame)
        await settle()

    async def flux_hears(self, *events: stt.SpeechEvent) -> None:
        self.ears.hears(*events)
        await settle()

    async def he_is(self, state: str) -> None:
        self.gate.agent_state_changed(state)
        await settle()

    async def __aenter__(self) -> "Session":
        self.running = asyncio.create_task(self.run())
        await settle()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.mic.put_nowait(None)  # the mic closes: the session ends
        await self.running

    async def run(self) -> None:
        async for event in self.gate.listen(self.frames(), FakeSpotter()):
            self.livekit.append(event)

    async def frames(self):
        while (mic_frame := await self.mic.get()) is not None:
            yield mic_frame


# --- Asleep and awake ---
async def test_asleep_flux_stays_closed():
    async with Session() as session:
        await session.say(*[frame() for _ in range(20)])
        await session.he_is("listening")
    assert session.flux == []
    assert session.livekit == []


async def test_hey_jarvis_opens_flux_with_the_audio_just_before_it():
    async with Session() as session:
        mic = [*(frame() for _ in range(9)), frame(WAKE)]
        await session.say(*mic)
        assert session.ears.pushed == mic[-4:]  # the pre-roll: "Hey Jarvis" itself
        assert session.ears.start_time_offset == 6 * FRAME  # Flux's clock starts where it does
        later = frame()
        await session.say(later)
        assert session.ears.pushed[-1] is later  # then everything live


async def test_with_no_pre_roll_flux_hears_only_what_comes_next():
    async with Session() as session:
        session.gate.timing = Timing(pre_roll=0)
        await session.say(frame(), frame(WAKE))
        assert session.ears.pushed == []


async def test_awake_livekit_gets_everything_flux_hears():
    async with Session() as session:
        await session.say(frame(WAKE))
        await session.flux_hears(start(), interim("What's"), final("What's my day?"), end())
    assert heard(session.livekit) == ["start", "interim: What's", "final: What's my day?", "end"]


async def test_hey_jarvis_then_nothing_goes_back_to_sleep():
    async with Session() as session:
        await session.say(frame(WAKE))
        await session.he_is("listening")
        await asyncio.sleep(FAST.after_wake / 2)
        assert session.gate.awake
        await asyncio.sleep(FAST.after_wake)
        assert not session.gate.awake
        assert session.ears.closed  # Flux stops billing
        await session.say(frame(WAKE))  # the spotter is still listening
        assert len(session.flux) == 2


async def test_he_never_falls_asleep_mid_reply():
    async with Session() as session:
        await session.say(frame(WAKE))
        for state in ("thinking", "speaking"):
            await session.he_is(state)
            await asyncio.sleep(FAST.after_wake * 1.5)
            assert session.gate.awake


async def test_after_he_answers_you_can_follow_up_for_a_few_seconds():
    async with Session() as session:
        await session.say(frame(WAKE))
        await session.he_is("speaking")
        await session.he_is("listening")
        await asyncio.sleep(FAST.follow_up / 2)
        assert session.gate.awake
        await asyncio.sleep(FAST.follow_up)  # shorter than the wait after "Hey Jarvis"
        assert not session.gate.awake


async def test_after_shall_i_you_get_longer_to_answer():
    async with Session() as session:
        session.question = True
        await session.say(frame(WAKE))
        await session.he_is("speaking")
        await session.he_is("listening")
        await asyncio.sleep(FAST.follow_up * 1.5)
        assert session.gate.awake
        await asyncio.sleep(FAST.question)
        assert not session.gate.awake


async def test_while_you_talk_he_stays_awake():
    async with Session() as session:
        await session.say(frame(WAKE))
        await session.flux_hears(start(), interim("Let me think"))
        await asyncio.sleep(FAST.after_wake * 1.5)
        assert session.gate.awake
        await session.flux_hears(final("Let me think about it."), end())
        await asyncio.sleep(FAST.after_wake * 1.5)
        assert not session.gate.awake


async def test_he_wakes_when_he_speaks_first_so_you_can_answer():
    async with Session() as session:
        await session.say(frame(), frame())
        await session.he_is("speaking")  # e.g. the greeting
        assert session.ears.pushed == []  # no pre-roll: you weren't talking to him
        assert session.ears.start_time_offset == 2 * FRAME
        live = frame()
        await session.say(live)
        assert session.ears.pushed == [live]


async def test_without_a_mic_his_speech_opens_nothing():
    async with Session() as session:  # the text-only debugger: no audio ever arrives
        await session.he_is("speaking")
    assert session.flux == []


async def test_if_flux_fails_he_sleeps_and_the_next_wake_word_retries():
    async with Session() as session:
        await session.say(frame(WAKE))
        session.ears.fail(RuntimeError("Deepgram is unreachable."))
        await settle()
        assert not session.gate.awake
        await session.say(frame(WAKE))
        assert len(session.flux) == 2
        assert session.gate.awake


async def test_a_turn_cut_short_by_flux_failing_is_closed_for_livekit():
    async with Session() as session:
        await session.say(frame(WAKE))
        await session.flux_hears(start(), interim("What's"))
        session.ears.fail(RuntimeError("Deepgram is unreachable."))
        await settle()
    assert heard(session.livekit) == ["start", "interim: What's", "end"]  # turn closed for LiveKit


# --- While he speaks: only "Jarvis", "stop" and "wait" get through ---
async def awake_and_speaking(session: Session) -> None:
    await session.say(frame(WAKE))
    await session.he_is("speaking")


async def test_words_said_over_him_are_ignored():
    async with Session() as session:
        await awake_and_speaking(session)
        await session.flux_hears(start(), interim("Lovely weather"), final("Lovely weather."))
        await session.flux_hears(end())
    assert session.livekit == []
    assert session.interrupted == 0


@pytest.mark.parametrize(
    "words",
    ["Stop.", "Wait, make it Thursday.", "Jarvis, what about Friday?", "Hey Jarvis, cancel that."],
)
async def test_jarvis_stop_or_wait_cut_him_off_and_reach_livekit(words):
    async with Session() as session:
        await awake_and_speaking(session)
        await session.flux_hears(start(), interim(words))
        assert session.interrupted == 1
        await session.he_is("listening")
        await session.flux_hears(final(words), end())
    assert heard(session.livekit) == ["start", f"interim: {words}", f"final: {words}", "end"]


async def test_the_spotter_stops_him_before_flux_has_heard_you():
    async with Session() as session:
        await awake_and_speaking(session)
        await session.say(frame(STOP))
        assert session.interrupted == 1
        # LiveKit lags a moment: Flux's turn may start while he still counts as speaking
        await session.flux_hears(start(), final("Stop."), end())
    assert heard(session.livekit) == ["start", "final: Stop.", "end"]


async def test_the_spotter_releases_a_turn_flux_had_started():
    async with Session() as session:
        await awake_and_speaking(session)
        await session.flux_hears(start(), interim("Hey"))
        assert session.livekit == []  # held: "Hey" could be anything
        await session.say(frame(WAKE))
        assert session.interrupted == 1
    assert heard(session.livekit) == ["start", "interim: Hey"]


async def test_stop_means_nothing_when_he_is_not_speaking():
    async with Session() as session:
        await session.say(frame(WAKE))
        await session.he_is("listening")
        await session.say(frame(STOP))  # e.g. "the bus stop" in your sentence
    assert session.interrupted == 0


async def test_an_answer_overlapping_his_last_words_gets_through():
    async with Session() as session:
        session.he_said = "Shall I add lunch with Sam on Friday?"
        await awake_and_speaking(session)
        await session.flux_hears(start(), interim("Yes"))
        await session.he_is("listening")
        await session.flux_hears(final("Yes, please."), end())
    assert heard(session.livekit) == ["start", "interim: Yes", "final: Yes, please.", "end"]


async def test_his_own_voice_coming_back_is_dropped():
    async with Session() as session:
        session.he_said = "Right away, sir."
        await awake_and_speaking(session)
        await session.flux_hears(start(), interim("Right away"))
        await session.he_is("thinking")
        await session.flux_hears(final("Right away, sir."), end())
    assert session.livekit == []


async def test_usage_reports_always_reach_livekit():
    usage = stt.SpeechEvent(
        Kind.RECOGNITION_USAGE, recognition_usage=stt.RecognitionUsage(audio_duration=5.0)
    )
    async with Session() as session:
        await awake_and_speaking(session)
        await session.flux_hears(start(), usage, interim("Lovely weather"))
    assert session.livekit == [usage]


# --- The words ---
@pytest.mark.parametrize(
    ("said", "cut_in"),
    [
        ("Stop.", True),
        ("wait, make it Thursday", True),
        ("Jarvis, what's up?", True),
        ("Hey Jarvis!", True),
        ("Actually, can you do it, Jarvis?", True),  # his name anywhere
        ("The bus stop.", False),
        ("I can't wait.", False),
        ("", False),
    ],
)
def test_which_words_may_cut_him_off(said, cut_in):
    assert cuts_in(said) is cut_in


@pytest.mark.parametrize(
    ("said", "only_wake"),
    [("Hey Jarvis.", True), ("Jarvis?", True), ("Hey.", False), ("Hey Jarvis, what's up?", False)],
)
def test_only_the_wake_words(said, only_wake):
    assert only_wake_words(said) is only_wake


@pytest.mark.parametrize(
    ("said", "only_cut_in"),
    [("Stop.", True), ("Jarvis, stop!", True), ("Wait.", True), ("Stop the music.", False)],
)
def test_only_stop_or_wait(said, only_cut_in):
    assert only_cut_in_words(said) is only_cut_in


def test_echo_is_his_own_words_coming_back():
    assert sounds_like("right away sir", "Right away, sir. You have two meetings.")
    assert not sounds_like("Yes, please.", "Shall I add lunch with Sam?")
    assert not sounds_like("", "Right away, sir.")
