"""The voice agent: LiveKit's `Agent`, with Jarvis's Brain plugged into its `llm_node`.

LiveKit listens (Deepgram Flux), hears when you've finished, and speaks (ElevenLabs). In between,
it calls `llm_node`: ours sends your words to `Brain.turn()` and streams his reply straight to the
voice, so he starts talking after his first few words. LiveKit's own LLM is only a stand-in.
Before any of that, `stt_node` runs your mic through the wake-word gate (gate.py).
For the orb, he tells the room whether he's awake, and carries the display tool's commands to the
desktop app (an RPC: the desktop does it and says what it did).
"""

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Coroutine
from contextlib import suppress
from typing import Any

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    FlushSentinel,
    ModelSettings,
    SpeechCreatedEvent,
    StopResponse,
    llm,
    stt,
)
from livekit.agents.utils.aio import cancel_and_wait
from livekit.agents.voice import SpeechHandle

from jarvis.auth.accounts import ACCOUNTS, Service, sign_in
from jarvis.brain.brain import Brain
from jarvis.brain.greeting import FILLER, SIR
from jarvis.config import Settings
from jarvis.display import AWAKE, DESKTOP, DISPLAY_METHOD, DisplayCommand
from jarvis.events import Event
from jarvis.voice.gate import (
    Finished,
    Gate,
    Timing,
    ends_conversation,
    only_cut_in_words,
    only_wake_words,
)
from jarvis.voice.phrases import Phrases, fixed_line
from jarvis.voice.spotter import Spotter

logger = logging.getLogger(__name__)

SNAG = "My apologies, sir, I've hit a snag. Do try that again."  # said when a turn fails
DISPLAY_SECONDS = 3.0  # how long the desktop app may take to show, hide or move the orb
NO_DESKTOP = "The desktop app isn't connected, so there's no orb to change."


class BrainLLM(llm.LLM):
    """A stand-in: LiveKit only calls `llm_node` when the session has an LLM. Jarvis's `llm_node`
    answers from the Brain, so this one is never asked anything."""

    @property
    def model(self) -> str:
        return "jarvis-brain"

    @property
    def provider(self) -> str:
        return "langgraph"

    def chat(self, *, chat_ctx: llm.ChatContext, **kwargs: Any) -> llm.LLMStream:
        raise NotImplementedError("Jarvis answers from his Brain (JarvisAgent.llm_node).")


class Reply:
    """One spoken reply as it's written: the Brain's words, plus the filler while agents work."""

    def __init__(self) -> None:
        self.pieces: asyncio.Queue[str | FlushSentinel | None] = asyncio.Queue()
        self.started = False  # has he said anything yet?

    def say(self, words: str) -> None:
        self.started = self.started or bool(words.strip())
        self.pieces.put_nowait(words)

    def fill_silence(self) -> None:
        """A sub-agent started: if he hasn't spoken yet, say the filler, now."""
        if not self.started:
            self.say(f"{FILLER} ")
            self.pieces.put_nowait(FlushSentinel())  # speak it now; don't wait for the rest

    def end(self) -> None:
        self.pieces.put_nowait(None)

    async def __aiter__(self) -> AsyncIterator[str | FlushSentinel]:
        while (piece := await self.pieces.get()) is not None:
            yield piece


class JarvisAgent(Agent):
    """Jarvis's voice. What he says comes from the Brain; his fixed lines come from recordings."""

    def __init__(
        self,
        brain: Brain,
        settings: Settings,
        greeting: str,
        phrases: Phrases,
        room: rtc.Room | None = None,
    ) -> None:
        super().__init__(instructions="Unused: Jarvis's personality and tools live in his Brain.")
        self.brain = brain
        self.settings = settings
        self.room = room  # the desktop app is in it (none in the console or tests)
        self.greeting = greeting
        self.phrases = phrases
        self.fixed_lines = {FILLER, SIR, greeting}
        self.one_at_a_time = asyncio.Lock()  # the Brain answers one message at a time
        self.reply: Reply | None = None  # the reply being written, if any
        self.newest_speech: SpeechHandle | None = None  # LiveKit's handle on the newest reply
        self.question_speech: SpeechHandle | None = None  # the reply that asked "Shall I…?"
        self.sign_ins_needed: set[Service] = set()
        self.signing_in: set[Service] = set()
        self.background: set[asyncio.Task[Any]] = set()
        self.said: deque[str] = deque(maxlen=200)  # his latest words: the gate spots his echo
        self.answering: Finished | None = None  # the spoken turn being answered (to time it)
        self.gate = Gate(
            self.open_ears,
            interrupt=self.stop_talking,
            question_open=lambda: brain.question is not None,
            his_words=lambda: "".join(self.said),
            awake_changed=self.show_awake,
            timing=Timing(follow_up=settings.voice.follow_up_seconds),
        )
        brain.events.subscribe(self.on_brain_event)
        brain.display = self.show_on_desktop  # the display tool's way to the orb

    async def on_enter(self) -> None:
        self.session.on("speech_created", self.on_speech_created)
        self.session.on("agent_state_changed", self.on_agent_state_changed)
        self.session.on("conversation_item_added", self.on_item_added)

    async def greet(self) -> None:
        """Say the boot greeting (from a recording, after the first time).

        Call it once the session has started: `on_enter` runs while the session is still setting
        up its audio, and a recording (which starts instantly) gets cut off there.
        """
        await self.phrases.record(self.fixed_lines)
        self.session.say(self.greeting, add_to_chat_ctx=False)

    def on_speech_created(self, event: SpeechCreatedEvent) -> None:
        if event.source == "generate_reply":  # a reply (not `say`): its llm_node runs next
            self.newest_speech = event.speech_handle

    def on_agent_state_changed(self, event: AgentStateChangedEvent) -> None:
        self.gate.agent_state_changed(event.new_state)  # speaking / thinking / listening

    def on_item_added(self, event: ConversationItemAddedEvent) -> None:
        """A reply to a spoken turn has been said: log how long it took to start, and why."""
        item = event.item
        if isinstance(item, llm.ChatMessage) and item.role == "assistant":
            finished, self.answering = self.answering, None
            if finished is not None and (line := latency(finished, item.metrics)):
                logger.info("Replied %s", line)

    # --- Ears: the wake-word gate ---
    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> AsyncIterator[stt.SpeechEvent]:
        """Your mic → the gate → only what's meant for Jarvis reaches LiveKit. Runs all session."""
        async for event in self.gate.listen(audio, Spotter()):
            yield event

    def open_ears(self) -> stt.RecognizeStream:
        """A new Deepgram Flux stream: the gate opens one each time he wakes."""
        if self.session.stt is None:
            raise RuntimeError("This voice session has no speech-to-text.")
        return self.session.stt.stream(conn_options=self.session.conn_options.stt_conn_options)

    def stop_talking(self) -> None:
        """You said "stop" or "Jarvis" over him."""
        with suppress(RuntimeError):  # nothing is playing
            self.session.interrupt()

    # --- The orb (in the desktop app) ---
    def show_awake(self, awake: bool) -> None:
        """The gate woke or fell asleep: tell the room, so the orb grows or shrinks."""
        if self.room is not None and self.room.isconnected():
            change = self.room.local_participant.set_attributes({AWAKE: str(awake).lower()})
            self.in_background(change)

    async def show_on_desktop(self, command: DisplayCommand) -> str:
        """Show, hide or move the orb: the desktop app does it and says what it did."""
        room = self.room
        if room is None or not room.isconnected() or DESKTOP not in room.remote_participants:
            return NO_DESKTOP
        try:
            return await room.local_participant.perform_rpc(
                destination_identity=DESKTOP,
                method=DISPLAY_METHOD,
                payload=command.to_json(),
                response_timeout=DISPLAY_SECONDS,
            )
        except rtc.RpcError as error:
            logger.warning("The desktop app couldn't change the orb: %s", error.message)
            return f"The desktop app couldn't do it: {error.message}"

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """You've finished a spoken turn. "Stop" ends the conversation (he stops listening); just
        "Hey Jarvis" gets "Sir?"; just "wait", silence (he keeps listening)."""
        words = new_message.text_content or ""
        if self.gate.drop_turn:  # cut off by "stop": not meant for him
            self.gate.drop_turn = False
            raise StopResponse()
        if ends_conversation(words):
            self.gate.end_conversation("you said “stop”")
            raise StopResponse()
        if only_wake_words(words):
            self.session.say(SIR, add_to_chat_ctx=False)
            raise StopResponse()
        if only_cut_in_words(words):
            raise StopResponse()  # he has stopped: nothing to answer
        self.answering = self.gate.finished

    def on_brain_event(self, event: Event) -> None:
        """What voice does while Jarvis works (text chat shows the same events its own way)."""
        if event.name == "agent_started" and self.reply is not None:
            self.reply.fill_silence()
        elif event.name == "sign_in_needed":
            self.sign_ins_needed.add(Service(event.details["service"]))
        elif event.name == "approval_needed":
            logger.info("Waiting for your OK: %s", " | ".join(event.details["changes"]))

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.FunctionTool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[str | FlushSentinel]:
        """Your words → the Brain → his reply, streamed to the voice as written. Never raises."""
        words = your_words(chat_ctx)
        speech = self.newest_speech  # this reply's handle: later it tells whether you heard it all
        async with self.one_at_a_time:
            if self.question_speech is not None and self.question_speech.interrupted:
                self.brain.withdraw_question()  # you talked over "Shall I…?": it can't be answered
            self.question_speech = None
            if not words.strip():
                return
            reply = self.reply = Reply()
            thinking = asyncio.create_task(self.think(words, reply, speech))
            try:
                async for piece in reply:
                    yield piece
            finally:
                self.reply = None
                await cancel_and_wait(thinking)  # cut off mid-reply: stop thinking too

    async def think(self, words: str, reply: Reply, speech: SpeechHandle | None) -> None:
        """Write the reply: the Brain's words, or an apology if anything goes wrong."""
        try:
            async for chunk in self.brain.turn(words):
                reply.say(chunk)
        except Exception:
            logger.exception("The Brain couldn't answer %r", words)
            reply.say(SNAG)
        finally:
            reply.end()
        if self.brain.question is not None:
            self.question_speech = speech  # it counts only if this reply plays to the end
        self.open_sign_ins()

    def open_sign_ins(self) -> None:
        """Open the browser for each sign-in this reply needs (chat shows a button instead)."""
        for service in self.sign_ins_needed - self.signing_in:
            self.signing_in.add(service)
            self.in_background(self.sign_in(service))
        self.sign_ins_needed.clear()

    def in_background(self, work: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(work)
        self.background.add(task)  # kept until done: asyncio holds tasks only weakly
        task.add_done_callback(self.background.discard)

    async def sign_in(self, service: Service) -> None:
        """Wait for you in the browser; once you're in, carry on with your request."""
        account = ACCOUNTS[service]
        try:
            await sign_in(self.settings, service)
        except Exception as error:
            logger.warning("%s sign-in failed: %s", account.name, error)
            self.session.say(f"The {account.name} sign-in didn't go through, sir.")
            return
        finally:
            self.signing_in.discard(service)
        self.session.generate_reply(user_input=account.signed_in)

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterator[rtc.AudioFrame]:
        """His fixed lines play from their recordings; everything else is spoken live."""
        text = self.remember(text)
        line, text = await fixed_line(text, self.phrases.recorded(self.fixed_lines))
        if line is not None:
            async for frame in self.phrases.play(line):
                yield frame
            return
        async for frame in Agent.default.tts_node(self, text, model_settings):
            yield frame

    async def remember(self, text: AsyncIterable[str]) -> AsyncIterator[str]:
        """Pass his words on to be spoken, keeping the latest ones for the gate."""
        async for chunk in text:
            self.said.append(chunk)
            yield chunk


def latency(finished: Finished, his: llm.MetricsReport) -> str | None:
    """How long a reply took to start, and where the time went: "2.20 s after you stopped
    (Flux 0.45 s · Brain 1.01 s · voice 0.17 s)". None if none of it was heard."""
    if "started_speaking_at" not in his:
        return None
    stages = {
        "Flux": finished.heard - finished.last_word,  # your last word → "you've finished"
        "Brain": his.get("llm_node_ttft"),  # your words → his first words
        "voice": his.get("tts_node_ttfb"),  # his first words → their first audio
    }
    parts = " · ".join(f"{name} {seconds:.2f} s" for name, seconds in stages.items() if seconds)
    return f"{his['started_speaking_at'] - finished.last_word:.2f} s after you stopped ({parts})"


def your_words(chat_ctx: llm.ChatContext) -> str:
    """Your latest message: what LiveKit heard you say (or what you typed in the console)."""
    for item in reversed(chat_ctx.items):
        if item.type == "message" and item.role == "user":
            return item.text_content or ""
    return ""
