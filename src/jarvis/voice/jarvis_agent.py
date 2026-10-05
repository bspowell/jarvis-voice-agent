"""The voice agent: LiveKit's `Agent`, with Jarvis's Brain plugged into its `llm_node`.

LiveKit listens (Deepgram Flux), hears when you've finished, and speaks (ElevenLabs). In between,
it calls `llm_node`: ours sends your words to `Brain.turn()` and streams his reply straight to the
voice, so he starts talking after his first few words. LiveKit's own LLM is only a stand-in.
"""

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from livekit import rtc
from livekit.agents import Agent, FlushSentinel, ModelSettings, SpeechCreatedEvent, llm
from livekit.agents.utils.aio import cancel_and_wait
from livekit.agents.voice import SpeechHandle

from jarvis.auth.accounts import ACCOUNTS, Service, sign_in
from jarvis.brain.brain import Brain
from jarvis.brain.greeting import FILLER
from jarvis.config import Settings
from jarvis.events import Event
from jarvis.voice.phrases import Phrases, fixed_line

logger = logging.getLogger(__name__)

SNAG = "My apologies, sir, I've hit a snag. Do try that again."  # said when a turn fails


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

    def __init__(self, brain: Brain, settings: Settings, greeting: str, phrases: Phrases) -> None:
        super().__init__(instructions="Unused: Jarvis's personality and tools live in his Brain.")
        self.brain = brain
        self.settings = settings
        self.greeting = greeting
        self.phrases = phrases
        self.fixed_lines = {FILLER, greeting}
        self.one_at_a_time = asyncio.Lock()  # the Brain answers one message at a time
        self.reply: Reply | None = None  # the reply being written, if any
        self.newest_speech: SpeechHandle | None = None  # LiveKit's handle on the newest reply
        self.question_speech: SpeechHandle | None = None  # the reply that asked "Shall I…?"
        self.sign_ins_needed: set[Service] = set()
        self.signing_in: set[Service] = set()
        self.background: set[asyncio.Task[None]] = set()
        brain.events.subscribe(self.on_brain_event)

    async def on_enter(self) -> None:
        self.session.on("speech_created", self.on_speech_created)

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
            task = asyncio.create_task(self.sign_in(service))
            self.background.add(task)
            task.add_done_callback(self.background.discard)
        self.sign_ins_needed.clear()

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
        line, text = await fixed_line(text, self.phrases.recorded(self.fixed_lines))
        if line is not None:
            async for frame in self.phrases.play(line):
                yield frame
            return
        async for frame in Agent.default.tts_node(self, text, model_settings):
            yield frame


def your_words(chat_ctx: llm.ChatContext) -> str:
    """Your latest message: what LiveKit heard you say (or what you typed in the console)."""
    for item in reversed(chat_ctx.items):
        if item.type == "message" and item.role == "user":
            return item.text_content or ""
    return ""
