from datetime import datetime
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.brain.brain import Brain, timestamped


def make_brain(*replies: str) -> Brain:
    """A Brain whose model is fake: scripted replies, no API calls, no cost."""
    model = GenericFakeChatModel(messages=iter([AIMessage(reply) for reply in replies]))
    agent = create_agent(model, tools=[], name="jarvis", checkpointer=InMemorySaver())
    return Brain(agent, thread_id="test")


async def collect(brain: Brain, text: str) -> list[str]:
    return [chunk async for chunk in brain.turn(text)]


async def test_reply_streams_in_chunks():
    brain = make_brain("Splendid weather for it, sir.")
    chunks = await collect(brain, "hello")
    assert len(chunks) > 1
    assert "".join(chunks) == "Splendid weather for it, sir."


async def test_thread_remembers_earlier_messages():
    brain = make_brain("First reply.", "Second reply.")
    await collect(brain, "one")
    await collect(brain, "two")
    state = await brain.agent.aget_state(brain.config)
    assert len(state.values["messages"]) == 4  # you, Jarvis, you, Jarvis


async def test_user_message_carries_the_time():
    brain = make_brain("Noted.")
    await collect(brain, "what time is it?")
    state = await brain.agent.aget_state(brain.config)
    assert state.values["messages"][0].content.startswith("[")


def test_timestamp_format():
    now = datetime(2026, 10, 3, 14, 5, tzinfo=ZoneInfo("America/Vancouver"))
    assert timestamped("hi", now) == "[Saturday 3 October 2026, 2:05 PM PDT] hi"
