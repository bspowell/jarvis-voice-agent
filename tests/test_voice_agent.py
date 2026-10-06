"""The voice agent, through LiveKit's own test harness (text only: no audio, no room).

The Brain is scripted, so these tests check the wiring between LiveKit and the Brain: what he
says, the filler, failures, unheard questions and sign-ins. The Brain itself has its own tests.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fakes import FakeFlux, FakeMic, FakeSpotter, FakeVoice, end, final, frame, start
from livekit import rtc
from livekit.agents import AgentSession, ModelSettings, SpeechCreatedEvent, llm
from livekit.agents.voice import SpeechHandle

from jarvis.brain.greeting import FILLER, SIR
from jarvis.config import Settings
from jarvis.display import (
    AWAKE,
    DESKTOP,
    DISPLAY_METHOD,
    EVENTS_TOPIC,
    SIGN_IN_METHOD,
    DisplayCommand,
)
from jarvis.events import Event, EventBus
from jarvis.voice import jarvis_agent
from jarvis.voice.gate import Finished
from jarvis.voice.jarvis_agent import (
    DISPLAY_SECONDS,
    NO_DESKTOP,
    SNAG,
    BrainLLM,
    JarvisAgent,
    latency,
    your_words,
)
from jarvis.voice.phrases import Phrases
from jarvis.voice.spotter import WAKE
from jarvis.voice.worker import TURNS

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


class FakeRoom:
    """Just enough of a LiveKit room: who's in it, and what the agent sends through it."""

    def __init__(self, *, desktop: bool = True, answer: str | Exception = "Done.") -> None:
        self.remote_participants = {DESKTOP: object()} if desktop else {}
        self.local_participant = self  # the agent's side: sets attributes, makes calls
        self.answer = answer  # the desktop app's reply to a call
        self.attributes: list[dict[str, str]] = []
        self.calls: list[dict] = []
        self.texts: list[dict] = []  # events sent as text: topic, to whom, the event
        self.methods: dict[str, object] = {}  # calls the agent takes from the desktop app

    def isconnected(self) -> bool:
        return True

    async def send_text(self, text: str, *, topic: str, destination_identities: list[str]) -> None:
        self.texts.append(
            {"topic": topic, "to": destination_identities, "event": Event.from_json(text)}
        )

    def register_rpc_method(self, method: str, handler) -> None:
        self.methods[method] = handler

    async def set_attributes(self, attributes: dict[str, str]) -> None:
        self.attributes.append(attributes)

    async def perform_rpc(self, **call) -> str:
        self.calls.append(call)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def make_agent(brain: FakeBrain, tmp_path, room: FakeRoom | None = None) -> JarvisAgent:
    phrases = Phrases(tmp_path, FakeVoice(), voice_name="test")
    return JarvisAgent(brain, Settings(_env_file=None), GREETING, phrases, room=room)


@asynccontextmanager
async def voice_session(brain: FakeBrain, tmp_path, room: FakeRoom | None = None):
    async with AgentSession(llm=BrainLLM()) as session:
        await session.start(make_agent(brain, tmp_path, room))
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
    assert set(agent.phrases.voice.lines) == {FILLER, SIR, GREETING}  # recorded for next time


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


def test_the_log_times_each_reply_from_your_last_word():
    finished = Finished(last_word=100.0, heard=100.45)  # Flux took 0.45 s to say you'd finished
    his = {"started_speaking_at": 102.2, "llm_node_ttft": 1.01, "tts_node_ttfb": 0.17}
    line = "2.20 s after you stopped (Flux 0.45 s · Brain 1.01 s · voice 0.17 s)"
    assert latency(finished, his) == line
    assert latency(finished, {"llm_node_ttft": 1.01}) is None  # cut off before any audio


@pytest.mark.parametrize("words", ["", "   "])
async def test_silence_gets_no_reply(tmp_path, words):
    brain = FakeBrain()
    agent = make_agent(brain, tmp_path)
    assert await say_to(agent, words) == ""
    assert brain.heard == []


# --- Speaking to him: mic → wake-word gate → Flux → LiveKit's turns → the Brain ---
@asynccontextmanager
async def spoken_session(brain: FakeBrain, tmp_path, monkeypatch):
    """A real LiveKit session that hears you: a fake mic and spotter, and a fake Flux."""
    monkeypatch.setattr(jarvis_agent, "Spotter", FakeSpotter)
    mic, flux = FakeMic(), FakeFlux()
    async with AgentSession(
        stt=flux,
        vad=None,
        llm=BrainLLM(),
        turn_handling=TURNS,
        session_close_transcript_timeout=0.1,  # on closing, don't wait 2 s for a last transcript
    ) as session:
        session.input.audio = mic
        said = []  # what he said with `session.say` (fixed lines)
        say = session.say
        monkeypatch.setattr(session, "say", lambda text, **kw: said.append(text) or say(text, **kw))
        agent = make_agent(brain, tmp_path)
        turns = []  # your turns, as LiveKit hands them to the agent
        completed = agent.on_user_turn_completed

        async def on_user_turn_completed(turn_ctx, new_message):
            turns.append(new_message.text_content)
            await completed(turn_ctx, new_message)

        monkeypatch.setattr(agent, "on_user_turn_completed", on_user_turn_completed)
        await session.start(agent)
        yield SimpleNamespace(mic=mic, flux=flux, said=said, turns=turns, agent=agent)


async def wake_and_say(voice: SimpleNamespace, words: str) -> None:
    """ "Hey Jarvis" (the spotter wakes him), then Flux hears `words` as one turn."""
    if not voice.flux.streams or voice.flux.streams[-1].closed:
        voice.mic.say(frame(), frame(WAKE))
        await wait_until(lambda: voice.flux.streams and not voice.flux.streams[-1].closed)
    voice.flux.streams[-1].hears(start(), final(words), end())


async def test_hey_jarvis_and_your_question_reach_the_brain(tmp_path, monkeypatch):
    brain = FakeBrain(["Sunny all day, sir."])
    async with spoken_session(brain, tmp_path, monkeypatch) as voice:
        await wake_and_say(voice, "Hey Jarvis, what's the weather?")
        await wait_until(lambda: brain.heard)
    assert brain.heard == ["Hey Jarvis, what's the weather?"]


async def test_hey_jarvis_alone_gets_sir_and_the_brain_hears_nothing(tmp_path, monkeypatch):
    brain = FakeBrain()
    async with spoken_session(brain, tmp_path, monkeypatch) as voice:
        await wake_and_say(voice, "Hey Jarvis.")
        await wait_until(lambda: voice.said)
    assert voice.said == [SIR]
    assert brain.heard == []


async def test_stop_ends_the_conversation(tmp_path, monkeypatch):
    brain = FakeBrain(["Half past three, sir."])
    async with spoken_session(brain, tmp_path, monkeypatch) as voice:
        await wake_and_say(voice, "Stop.")
        # LiveKit waits 0.3 s before closing a turn; words before then join it ("Stop. What's…")
        await wait_until(lambda: voice.turns)
        await wait_until(lambda: voice.flux.streams[-1].closed)  # he has stopped listening
        await wake_and_say(voice, "What's the time?")  # so this takes "Hey Jarvis" again
        await wait_until(lambda: brain.heard)
    assert voice.turns == ["Stop.", "What's the time?"]
    assert brain.heard == ["What's the time?"]  # "Stop." never reached him
    assert len(voice.flux.streams) == 2
    assert voice.said == []


async def test_wait_alone_gets_no_reply_and_he_keeps_listening(tmp_path, monkeypatch):
    brain = FakeBrain(["Half past three, sir."])
    async with spoken_session(brain, tmp_path, monkeypatch) as voice:
        await wake_and_say(voice, "Wait.")
        await wait_until(lambda: voice.turns)
        assert not voice.flux.streams[-1].closed
        await wake_and_say(voice, "What's the time?")  # no "Hey Jarvis" needed
        await wait_until(lambda: brain.heard)
    assert brain.heard == ["What's the time?"]
    assert len(voice.flux.streams) == 1


async def test_a_turn_cut_off_by_stop_is_never_answered(tmp_path, monkeypatch):
    brain = FakeBrain(["Not a fan, sir."])
    async with spoken_session(brain, tmp_path, monkeypatch) as voice:
        voice.mic.say(frame(), frame(WAKE))
        await wait_until(lambda: voice.flux.streams)
        voice.flux.streams[-1].hears(start(), final("Did you see the game"))  # someone nearby
        await asyncio.sleep(0.05)
        voice.agent.gate.end_conversation("you said “stop”")
        await wait_until(lambda: voice.turns)
        await asyncio.sleep(0.1)
    assert voice.turns == ["Did you see the game"]
    assert brain.heard == []


# --- The orb ---
async def test_display_commands_go_to_the_desktop_and_its_answer_comes_back(tmp_path):
    room = FakeRoom(answer="The orb is hidden.")
    brain = FakeBrain()
    make_agent(brain, tmp_path, room)
    assert await brain.display(DisplayCommand("hide")) == "The orb is hidden."
    assert room.calls == [
        {
            "destination_identity": DESKTOP,
            "method": DISPLAY_METHOD,
            "payload": DisplayCommand("hide").to_json(),
            "response_timeout": DISPLAY_SECONDS,
        }
    ]


async def test_with_no_desktop_app_the_tool_hears_there_is_no_orb(tmp_path):
    for room in (FakeRoom(desktop=False), None):  # the desktop app is closed / the console
        brain = FakeBrain()
        make_agent(brain, tmp_path, room)
        assert await brain.display(DisplayCommand("hide")) == NO_DESKTOP


async def test_a_failed_display_command_is_reported_not_raised(tmp_path):
    timeout = rtc.RpcError(rtc.RpcError.ErrorCode.RESPONSE_TIMEOUT, "Response timeout")
    brain = FakeBrain()
    make_agent(brain, tmp_path, FakeRoom(answer=timeout))
    assert "Response timeout" in await brain.display(DisplayCommand("hide"))


async def test_the_room_hears_when_he_wakes_and_sleeps(tmp_path):
    room = FakeRoom()
    agent = make_agent(FakeBrain(), tmp_path, room)
    agent.show_awake(True)
    agent.show_awake(False)
    await asyncio.sleep(0)
    assert room.attributes == [{AWAKE: "true"}, {AWAKE: "false"}]


# --- The cards beside the orb ---
def click(service: str) -> rtc.RpcInvocationData:
    """The desktop app calls: you clicked a sign-in card's button."""
    return rtc.RpcInvocationData("call-1", DESKTOP, service, 3.0, SIGN_IN_METHOD)


async def test_the_brains_events_go_to_the_desktop_app_as_text(tmp_path):
    weather = Event("weather", {"place": "Vancouver"})
    for room, sent in ((FakeRoom(), 1), (FakeRoom(desktop=False), 0)):
        brain = FakeBrain([weather, "Rain at four, sir."])
        async with voice_session(brain, tmp_path, room) as session:
            await reply_to(session, "Will it rain?")
            await asyncio.sleep(0.01)
        assert room.texts == [{"topic": EVENTS_TOPIC, "to": [DESKTOP], "event": weather}] * sent


async def test_with_the_desktop_app_a_sign_in_waits_for_your_click(tmp_path, monkeypatch):
    opened = []

    async def sign_in(settings, service):
        opened.append(service)  # the browser opens; you approve

    monkeypatch.setattr(jarvis_agent, "sign_in", sign_in)
    room = FakeRoom()
    brain = FakeBrain([NEEDS_GOOGLE, "The button's on your screen, sir."], ["Noon, sir."])
    async with voice_session(brain, tmp_path, room) as session:
        session.current_agent.serve_desktop()
        await reply_to(session, "When's lunch tomorrow?")
        await asyncio.sleep(0.05)
        assert opened == []  # no browser yet: the card has the button
        answer = await room.methods[SIGN_IN_METHOD](click("google"))
        await wait_until(lambda: len(brain.heard) == 2)
    assert answer == "Waiting for you to sign in to Google in the browser."
    assert opened == ["google"]
    assert brain.heard == ["When's lunch tomorrow?", "I've signed in to Google."]
    sent = [(text["event"].name, text["event"].details) for text in room.texts]
    assert sent == [("sign_in_needed", {"service": "google"}), ("signed_in", {"service": "google"})]


async def test_a_click_for_an_unknown_account_is_refused(tmp_path):
    room = FakeRoom()
    make_agent(FakeBrain(), tmp_path, room).serve_desktop()
    with pytest.raises(rtc.RpcError):
        await room.methods[SIGN_IN_METHOD](click("myspace"))


async def test_stop_clears_the_cards(tmp_path):
    room = FakeRoom(answer="Cleared the cards.")
    agent = make_agent(FakeBrain(), tmp_path, room)
    agent.gate.end_conversation("you said “stop”")
    await wait_until(lambda: room.calls)
    assert room.calls[0]["payload"] == DisplayCommand("clear").to_json()
