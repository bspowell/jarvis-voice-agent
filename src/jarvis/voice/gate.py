"""The wake-word gate: Jarvis only listens to you after "Hey Jarvis".

LiveKit hands the agent's `stt_node` every frame from your mic. The gate decides what reaches
Deepgram Flux (his ears) and which of Flux's words reach LiveKit:
- Asleep: only the spotter listens (on your Mac, free). Flux is closed: it bills while connected.
- "Hey Jarvis" → awake: Flux opens and first hears the last 1.5 s, so it gets "Hey Jarvis,
  what's…" whole, then everything after. He also wakes when he speaks first (the greeting).
- While he speaks (echo guard): your words stop him only if they name him ("…, Jarvis?") or
  start with "stop" or "wait" (the spotter hears "Hey Jarvis" and "stop" before Flux does).
  Anything else said over him is ignored, and his own voice leaking back is dropped.
- "Stop" ends the conversation: he stops talking and listening, and a turn already under way
  (someone talking nearby) is dropped. "Wait" only stops him talking.
- Quiet: 6 s after he answers (10 s after "Shall I…?", 8 s after the wake word alone) → asleep.
"""

import asyncio
import itertools
import logging
import re
import time
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import Enum

from livekit import rtc
from livekit.agents import stt

from jarvis.voice.audio import decibels
from jarvis.voice.spotter import STOP, WAKE, Spotter

logger = logging.getLogger(__name__)

WAKE_WORDS = {"hey", "jarvis"}
CUT_IN_WORDS = {"jarvis", "stop", "wait"}  # the only words that may cut him off
BUSY = {"thinking", "speaking"}  # LiveKit's agent states while he's on a reply
SPEECH_RISE = 15.0  # dB above the pre-roll's quietest moment: someone has started talking
FLUX_BEHIND = 2.0  # seconds Flux may lag your voice before the log says why he's slow


@dataclass(frozen=True)
class Timing:
    """How long the gate waits, in seconds."""

    pre_roll: float = 1.5  # audio from before the wake word that Flux hears first
    lead_in: float = 0.25  # of the silence before you spoke in it, what Flux still hears
    after_wake: float = 8.0  # "Hey Jarvis", then nothing: back to sleep
    follow_up: float = 6.0  # after his answer: carry on without the wake word
    question: float = 10.0  # after "Shall I…?": time to answer


@dataclass(frozen=True)
class Finished:
    """When you finished your latest turn (wall-clock times): to time his reply."""

    last_word: float  # your last word ended (from Flux's word timings)
    heard: float  # Flux said you'd finished


class Turn(Enum):
    """What happens to the words of the turn Flux is hearing."""

    PASS = "pass"  # LiveKit gets them as they come
    HOLD = "hold"  # started while he spoke: kept back until it's clear they're for him


class Gate:
    """Opens Flux on "Hey Jarvis", closes it when the conversation goes quiet, and filters what
    LiveKit hears while he's talking. One gate per voice session."""

    def __init__(
        self,
        open_ears: Callable[[], stt.RecognizeStream],
        *,
        interrupt: Callable[[], None],
        question_open: Callable[[], bool],
        his_words: Callable[[], str],
        awake_changed: Callable[[bool], None] = lambda awake: None,
        stopped: Callable[[], None] = lambda: None,
        timing: Timing | None = None,
    ) -> None:
        self.open_ears = open_ears  # a new Flux stream
        self.interrupt = interrupt  # stop him talking, now
        self.question_open = question_open  # is "Shall I…?" waiting for an answer?
        self.his_words = his_words  # what he said lately (to recognise his echo)
        self.awake_changed = awake_changed  # he woke (True) or fell asleep (False): for the orb
        self.stopped = stopped  # you said "stop" (the conversation's over): the cards go too
        self.timing = timing or Timing()
        self.ears: stt.RecognizeStream | None = None  # Flux, while awake
        self.events: asyncio.Queue[stt.SpeechEvent | None] | None = None  # to LiveKit
        self.recent: deque[rtc.AudioFrame] = deque()  # the last `pre_roll` seconds of mic
        self.recent_seconds = 0.0
        self.mic_started_at: float | None = None  # wall-clock time your mic audio began
        self.clock = 0.0  # seconds of mic audio since then
        self.hearing: asyncio.Task[None] | None = None  # reads Flux's events while awake
        self.agent_state = "initializing"  # LiveKit's: listening / thinking / speaking
        self.he_spoke = False  # has he said anything since waking?
        self.you_talking = False  # is Flux in the middle of a turn?
        self.turn = Turn.PASS
        self.held: list[stt.SpeechEvent] = []  # a held turn's events, in order
        self.last_word: float | None = None  # when the turn's last word so far ended
        self.finished: Finished | None = None  # your latest finished turn
        self.flux_behind = False  # has the log said Flux is lagging? (once, until it catches up)
        self.pass_next_turn = False  # you cut in before Flux heard you: let your turn through
        self.drop_turn = False  # "stop" cut off a turn LiveKit had heard start: don't answer it
        self.quiet_timer: asyncio.TimerHandle | None = None
        self.closing: set[asyncio.Task[None]] = set()

    @property
    def awake(self) -> bool:
        return self.ears is not None

    @property
    def he_is_speaking(self) -> bool:
        return self.agent_state == "speaking"

    # --- The session: mic frames in, speech events out ---
    async def listen(
        self, audio: AsyncIterable[rtc.AudioFrame], spotter: Spotter
    ) -> AsyncIterator[stt.SpeechEvent]:
        """Run for the whole session (`stt_node`): every mic frame in, LiveKit's events out."""
        events = self.events = asyncio.Queue()
        reading = asyncio.create_task(self.read(audio, spotter))
        reading.add_done_callback(lambda _: events.put_nowait(None))  # mic closed: session over
        try:
            while (event := await events.get()) is not None:
                yield event
            reading.result()  # raise what stopped the mic, if anything did
        finally:
            self.events = None
            reading.cancel()
            self.sleep("the session ended")
            await asyncio.gather(reading, *self.closing, return_exceptions=True)

    async def read(self, audio: AsyncIterable[rtc.AudioFrame], spotter: Spotter) -> None:
        async for frame in audio:
            if self.mic_started_at is None:
                self.mic_started_at = time.time() - frame.duration  # as LiveKit times it
                if self.he_spoke:  # in a room he greets as he joins, before your audio arrives
                    self.wake("he spoke first; your mic just started", pre_roll=False)
            self.clock += frame.duration
            self.recent.append(frame)
            self.recent_seconds += frame.duration
            while self.recent and self.recent_seconds - self.recent[0].duration >= (
                self.timing.pre_roll
            ):
                self.recent_seconds -= self.recent.popleft().duration
            if self.ears is not None:
                with suppress(RuntimeError):  # Flux has failed: `hear_from` puts him to sleep
                    self.ears.push_frame(frame)
            self.react(spotter.hear(frame))

    def react(self, heard: set[str]) -> None:
        """The spotter heard a wake word: wake up, or cut him off. "Stop" while he's quiet means
        nothing here ("the bus stop"): only a turn that is just "stop" ends the conversation."""
        if not heard:
            return
        if not self.awake:
            if WAKE in heard:
                self.wake("heard “Hey Jarvis”", pre_roll=True)
        elif self.he_is_speaking:
            if STOP in heard:
                self.end_conversation("heard “stop”")
            else:
                self.cut_in("heard “Hey Jarvis”")

    def agent_state_changed(self, state: str) -> None:
        """LiveKit's agent state (from the session's `agent_state_changed` event)."""
        self.agent_state = state
        if state == "speaking":
            self.he_spoke = True
            self.pass_next_turn = False  # a new reply: an earlier "stop" is used up
            if not self.awake:
                self.wake("he's speaking", pre_roll=False)  # so you can answer him
        self.update_quiet_timer()

    # --- Awake and asleep ---
    def wake(self, reason: str, *, pre_roll: bool) -> None:
        """Open Flux. With `pre_roll`, it first hears the audio that held the wake word."""
        if self.awake or self.events is None or self.mic_started_at is None:
            return  # already listening, or no mic yet (e.g. the text-only debugger)
        ears = self.open_ears()
        first = from_your_voice(list(self.recent), self.timing.lead_in) if pre_roll else []
        # Flux times words from the first audio it gets; LiveKit times your turns from them.
        ears.start_time_offset = max(0.0, self.clock - sum(frame.duration for frame in first))
        for frame in first:
            ears.push_frame(frame)
        self.ears = ears
        self.he_spoke = self.he_is_speaking
        self.you_talking = False
        self.turn = Turn.PASS
        self.held = []
        self.pass_next_turn = False
        self.drop_turn = False
        self.hearing = asyncio.create_task(self.hear_from(ears))
        logger.info("Listening (%s).", reason)
        self.awake_changed(True)
        self.update_quiet_timer()

    def end_conversation(self, reason: str) -> None:
        """You said "stop": he stops talking and stops listening, at once. A turn LiveKit has
        heard start (often someone else talking nearby) is closed and marked to be dropped."""
        logger.info("Conversation over: %s.", reason)
        if self.he_is_speaking:
            self.interrupt()
        self.drop_turn = self.you_talking and self.turn is Turn.PASS
        self.sleep(reason)
        self.stopped()

    def sleep(self, reason: str) -> None:
        """Close Flux; only the spotter listens again."""
        self.cancel_quiet_timer()
        ears, self.ears = self.ears, None
        if ears is None:
            return
        if self.you_talking and self.turn is Turn.PASS:  # LiveKit heard you start: close the turn
            self.send(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
        self.you_talking = False
        self.held = []
        closing = asyncio.create_task(ears.aclose())
        self.closing.add(closing)
        closing.add_done_callback(self.closing.discard)
        logger.info("Asleep (%s).", reason)
        self.awake_changed(False)

    async def hear_from(self, ears: stt.RecognizeStream) -> None:
        """Pass Flux's events through the gate until the stream closes."""
        try:
            async for event in ears:
                if ears is not self.ears:
                    break
                self.on_flux_event(event)
        except Exception as error:  # Flux gave up (after its own retries): the next wake retries
            logger.warning("Deepgram Flux stopped: %s", error)
        finally:
            if ears is self.ears:
                self.sleep("Flux stopped")

    # --- Flux's events: which ones LiveKit gets ---
    def on_flux_event(self, event: stt.SpeechEvent) -> None:
        kind = stt.SpeechEventType
        if event.type is kind.RECOGNITION_USAGE:  # how much audio Flux billed: always passes
            self.send(event)
            return
        self.check_lag(event)
        if event.type is kind.START_OF_SPEECH:
            self.you_talking = True
            over_him = self.he_is_speaking and not self.pass_next_turn
            self.turn = Turn.HOLD if over_him else Turn.PASS
            self.pass_next_turn = False
        elif event.type is kind.FINAL_TRANSCRIPT:
            self.last_word = self.last_word_end(event) or self.last_word
        elif event.type is kind.END_OF_SPEECH and self.last_word is not None:
            # Flux's end of turn carries no time, so LiveKit would count its 0.3 s wait from
            # now. Stamped with your last word, the wait has usually passed: he answers sooner.
            event = replace(event, speech_end_time=self.last_word)
            self.finished = Finished(self.last_word, heard=time.time())
            self.last_word = None
        if self.turn is Turn.PASS:
            self.send(event)
        elif self.turn is Turn.HOLD:
            self.held.append(event)
            if event.alternatives and cuts_in(event.alternatives[0].text):
                self.cut_in(f"you said “{event.alternatives[0].text}”")
        if event.type is kind.END_OF_SPEECH:
            self.you_talking = False
            if self.turn is Turn.HOLD:
                self.settle_held_turn()
            self.turn = Turn.PASS
        self.update_quiet_timer()

    def check_lag(self, event: stt.SpeechEvent) -> None:
        """Log when Flux's results come well behind your voice: a slow or busy network (Flux is
        in Deepgram's cloud). He then answers late, and may miss a follow-up."""
        heard_up_to = event.alternatives[0].end_time if event.alternatives else 0.0
        if not heard_up_to:
            return
        behind = self.clock - heard_up_to  # both count seconds of your audio
        if behind > FLUX_BEHIND and not self.flux_behind:
            self.flux_behind = True
            logger.warning("Deepgram is %.1f s behind your voice: a slow network?", behind)
        elif behind < FLUX_BEHIND / 2:
            self.flux_behind = False  # caught up: say so again if it falls behind again

    def last_word_end(self, event: stt.SpeechEvent) -> float | None:
        """When the transcript's last word ended, on LiveKit's (wall) clock."""
        words = event.alternatives[0].words if event.alternatives else None
        ended = words[-1].end_time if words else None
        if self.mic_started_at is None or not isinstance(ended, float):
            return None
        return self.mic_started_at + ended  # Flux's times count from where the mic began

    def cut_in(self, reason: str) -> None:
        """You said "stop" or "Jarvis" over him: he stops, and LiveKit gets your words."""
        logger.info("Stopping him: %s.", reason)
        self.interrupt()
        if self.turn is Turn.HOLD and self.you_talking:
            self.turn = Turn.PASS
            self.send(*self.held)
            self.held = []
        elif not self.you_talking:
            self.pass_next_turn = True  # Flux hasn't heard you start yet

    def settle_held_turn(self) -> None:
        """A turn said over him has ended without "Jarvis/stop/wait": keep it or drop it."""
        held, self.held = self.held, []
        said = final_text(held)
        if self.he_is_speaking:
            logger.info("Ignored, said over him: “%s”.", said)
        elif sounds_like(said, self.his_words()):
            logger.info("Ignored his own echo: “%s”.", said)
        else:  # you answered as he finished
            self.send(*held)

    def send(self, *events: stt.SpeechEvent) -> None:
        if self.events is not None:
            for event in events:
                self.events.put_nowait(event)

    # --- Going back to sleep when it's quiet ---
    def update_quiet_timer(self) -> None:
        quiet = self.awake and self.agent_state not in BUSY and not self.you_talking
        if not quiet:
            self.cancel_quiet_timer()
        elif self.quiet_timer is None:
            seconds = self.listening_window()
            self.quiet_timer = asyncio.get_running_loop().call_later(
                seconds, self.sleep, f"{seconds:g} s of quiet"
            )

    def listening_window(self) -> float:
        if self.question_open():
            return self.timing.question
        return self.timing.follow_up if self.he_spoke else self.timing.after_wake

    def cancel_quiet_timer(self) -> None:
        if self.quiet_timer is not None:
            self.quiet_timer.cancel()
            self.quiet_timer = None


def from_your_voice(frames: list[rtc.AudioFrame], lead_in: float) -> list[rtc.AudioFrame]:
    """The pre-roll from just before you spoke: of the silence before your voice, only
    `lead_in` seconds are kept. Flux catches up on the pre-roll only a little faster than real
    time, so every second it skips is a second sooner for your first reply."""
    levels = [decibels(frame) for frame in frames]
    voice = min(levels, default=0.0) + SPEECH_RISE
    start = next((i for i, level in enumerate(levels) if level > voice), 0)  # none: keep all
    kept = 0.0
    while start > 0 and kept < lead_in:
        start -= 1
        kept += frames[start].duration
    return frames[start:]


def words(text: str) -> list[str]:
    """Lower-case words without punctuation: "Hey, Jarvis!" → ["hey", "jarvis"]."""
    return re.findall(r"[a-z0-9']+", text.lower())


def cuts_in(text: str) -> bool:
    """May it cut him off? His name anywhere ("Actually, Jarvis…", "…, Jarvis?"), or "stop" or
    "wait" first. Those two only count first: they turn up in ordinary sentences, and his echo."""
    said = words(text)
    if "jarvis" in said:
        return True
    if said[:1] == ["hey"]:
        said = said[1:]
    return bool(said) and said[0] in CUT_IN_WORDS


def ends_conversation(text: str) -> bool:
    """Is it "stop"? First ("Stop.", "Hey Jarvis, stop", "Stop the music") or right by his name
    ("…, Jarvis, stop"). Anywhere else it's an ordinary word ("Where's the bus stop?")."""
    said = words(text)
    first = next((word for word in said if word not in WAKE_WORDS), None)
    by_his_name = any({a, b} == {"jarvis", "stop"} for a, b in itertools.pairwise(said))
    return first == "stop" or by_his_name


def only_wake_words(text: str) -> bool:
    """Just "Hey Jarvis" (or "Jarvis"), nothing asked yet."""
    said = words(text)
    return "jarvis" in said and set(said) <= WAKE_WORDS


def only_cut_in_words(text: str) -> bool:
    """Just "wait" (or "stop", maybe with his name): nothing to answer."""
    said = words(text)
    return bool(said) and set(said) <= WAKE_WORDS | CUT_IN_WORDS


def sounds_like(heard: str, his_words: str) -> bool:
    """Is every word Flux heard one he just said? Then it's his voice coming back (echo)."""
    said = set(words(heard))
    return bool(said) and said <= set(words(his_words))


def final_text(events: list[stt.SpeechEvent]) -> str:
    """The latest transcript in a turn's events (Flux's transcripts grow as you speak)."""
    for event in reversed(events):
        if event.alternatives:
            return event.alternatives[0].text
    return ""
