"""The voice agent, through LiveKit's own test harness (text only: no audio, no room).

The Brain is scripted, so these tests check the wiring between LiveKit and the Brain: what he
says, the filler, failures, unheard questions and sign-ins. The Brain itself has its own tests.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from fakes import FakeVoice
from livekit.agents import AgentSession, ModelSettings, SpeechCreatedEvent, llm
from livekit.agents.voice import SpeechHandle

from jarvis.brain.greeting import FILLER
from jarvis.config import Settings
from jarvis.events import Event, EventBus
from jarvis.voice import jarvis_agent
from jarvis.voice.jarvis_agent import SNAG, BrainLLM, JarvisAgent, your_words
from jarvis.voice.phrases import Phrases

ASKS = object()  # in a script: the Brain has just asked "Shall I…?" (it waits for the answer)
GREETING = "Good evening, sir. Three Agents all present and accounted for."


class FakeBrain:
    """Stands in for the Brain: each turn plays the next script, recording what it heard.

    A script holds words to say, events to publish, an exception to raise, or `ASKS`.
    """

    def __init__(self, *scripts: list) -> None:
        self.events = EventBus()
        self.question: str | None = None
        self.heard: list[str] = []
        self.scripts = list(scripts)

    async def turn(self, text: str):
        self.heard.append(text)
        self.question = None  # each message answers at most one question, like the real Brain
        for step in self.scripts.pop(0):
            if isinstance(step, Event):
                self.events.publish(step)
            elif isinstance(step, Exception):
                raise step
            elif step is ASKS:
                self.question = "Shall I?"
            else:
                yield step

    def withdraw_question(self) -> None:
        self.heard.append("(question withdrawn)")
        self.question = None


def make_agent(brain: FakeBrain, tmp_path) -> JarvisAgent:
    phrases = Phrases(tmp_path, FakeVoice(), voice_name="test")
    return JarvisAgent(brain, Settings(_env_file=None), GREETING, phrases)


@asynccontextmanager
async def voice_session(brain: FakeBrain, tmp_path):
    async with AgentSession(llm=BrainLLM()) as session:
        await session.start(make_agent(brain, tmp_path))
        yield session


async def reply_to(session: AgentSession, words: str) -> str:
    result = await session.run(user_input=words)
    return result.expect.next_event().is_message(role="assistant").event().item.text_content


async def test_his_reply_is_the_brains_words(tmp_path):
    brain = FakeBrain(["Rain ", "at four, ", "sir."])
    async with voice_session(brain, tmp_path) as session:
        assert await reply_to(session, "Will it rain?") == "Rain at four, sir."
    assert brain.heard == ["Will it rain?"]


async def test_the_filler_fills_the_silence_while_a_sub_agent_works(tmp_path):
    brain = FakeBrain([Event("agent_started", {"agent": "calendar"}), "Two meetings, sir."])
    async with voice_session(brain, tmp_path) as session:
        assert await reply_to(session, "What's on Friday?") == f"{FILLER} Two meetings, sir."


async def test_no_filler_once_he_has_started_talking(tmp_path):
    brain = FakeBrain(["Let me look. ", Event("agent_started", {"agent": "notes"}), "Found it."])
    async with voice_session(brain, tmp_path) as session:
        assert await reply_to(session, "Find my trek notes") == "Let me look. Found it."


async def test_a_failed_turn_apologises_and_never_ends_the_session(tmp_path):
    # LiveKit closes a session after 3 errors in a row: a failed turn must never count as one.
    failures = [[RuntimeError("Anthropic is overloaded.")] for _ in range(3)]
    brain = FakeBrain(*failures, ["Still here, sir."])
    async with voice_session(brain, tmp_path) as session:
        for _ in range(3):
            assert await reply_to(session, "Hello?") == SNAG
        assert await reply_to(session, "Hello?") == "Still here, sir."


async def test_he_greets_you_from_a_recording_once_the_session_has_started(tmp_path):
    agent = make_agent(FakeBrain(), tmp_path)
    async with AgentSession(llm=BrainLLM()) as session:
        said = []
        session.on("speech_created", lambda event: said.append(event.source))
        await session.start(agent)
        assert said == []  # not while the session is still setting up its audio
        await agent.greet()
        assert said == ["say"]
    assert set(agent.phrases.voice.lines) == {FILLER, GREETING}  # recorded for next time


# --- "Shall I…?" only counts if you heard all of it ---
def speech_created(agent: JarvisAgent) -> SpeechHandle:
    """LiveKit announces a new reply (as it does before each `llm_node` runs)."""
    speech = SpeechHandle.create()
    event = SpeechCreatedEvent(speech_handle=speech, user_initiated=False, source="generate_reply")
    agent.on_speech_created(event)
    return speech


async def say_to(agent: JarvisAgent, words: str) -> str:
    chat = llm.ChatContext()
    chat.add_message(role="user", content=words)
    pieces = [piece async for piece in agent.llm_node(chat, [], ModelSettings())]
    return "".join(piece for piece in pieces if isinstance(piece, str))


async def test_a_question_you_talked_over_cannot_be_answered(tmp_path):
    brain = FakeBrain(["Shall I book lunch with Sam?", ASKS], ["Yes to what, sir?"])
    agent = make_agent(brain, tmp_path)
    question = speech_created(agent)
    await say_to(agent, "Book lunch with Sam")
    question.interrupt()  # you spoke over him before he finished asking
    speech_created(agent)
    await say_to(agent, "Yes")
    assert brain.heard == ["Book lunch with Sam", "(question withdrawn)", "Yes"]


async def test_a_question_you_heard_to_the_end_waits_for_your_answer(tmp_path):
    brain = FakeBrain(["Shall I book lunch with Sam?", ASKS], ["Done, sir."])
    agent = make_agent(brain, tmp_path)
    speech_created(agent)
    await say_to(agent, "Book lunch with Sam")
    speech_created(agent)
    assert await say_to(agent, "Yes") == "Done, sir."
    assert brain.heard == ["Book lunch with Sam", "Yes"]  # nothing withdrawn: "Yes" answers it


# --- Sign-ins by voice: the browser opens, then he carries on ---
NEEDS_GOOGLE = Event("sign_in_needed", {"service": "google"})


async def wait_until(condition, seconds: float = 5.0) -> None:
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.01)


async def test_after_signing_in_he_carries_on_with_your_request(tmp_path, monkeypatch):
    opened = []

    async def sign_in(settings, service):
        opened.append(service)  # you approve in the browser

    monkeypatch.setattr(jarvis_agent, "sign_in", sign_in)
    brain = FakeBrain(
        [NEEDS_GOOGLE, "The Google sign-in is waiting for you, sir."], ["Lunch is at noon, sir."]
    )
    async with voice_session(brain, tmp_path) as session:
        await reply_to(session, "When's lunch tomorrow?")
        await wait_until(lambda: len(brain.heard) == 2)
    assert opened == ["google"]
    assert brain.heard == ["When's lunch tomorrow?", "I've signed in to Google."]


async def test_a_failed_sign_in_is_mentioned_and_nothing_is_retried(tmp_path, monkeypatch):
    async def sign_in(settings, service):
        raise RuntimeError("No answer from the browser within five minutes.")

    monkeypatch.setattr(jarvis_agent, "sign_in", sign_in)
    brain = FakeBrain([NEEDS_GOOGLE, "The Google sign-in is waiting for you, sir."])
    async with voice_session(brain, tmp_path) as session:
        await reply_to(session, "When's lunch tomorrow?")
        said = "The Google sign-in didn't go through, sir."
        await wait_until(lambda: said in [item.text_content for item in messages(session)])
    assert brain.heard == ["When's lunch tomorrow?"]


def messages(session: AgentSession) -> list[llm.ChatMessage]:
    return [item for item in session.history.items if item.type == "message"]


def test_your_words_are_the_latest_thing_you_said():
    chat = llm.ChatContext()
    chat.add_message(role="user", content="What's the weather?")
    chat.add_message(role="assistant", content="Sunny, sir.")
    chat.add_message(role="user", content="And tomorrow?")
    assert your_words(chat) == "And tomorrow?"


@pytest.mark.parametrize("words", ["", "   "])
async def test_silence_gets_no_reply(tmp_path, words):
    brain = FakeBrain()
    agent = make_agent(brain, tmp_path)
    assert await say_to(agent, words) == ""
    assert brain.heard == []
