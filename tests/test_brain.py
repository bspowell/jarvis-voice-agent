from datetime import datetime
from zoneinfo import ZoneInfo

from fakes import FakeToolCallingModel
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolCall
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.brain.brain import Brain, timestamped


def make_brain(*replies: str | AIMessage, tools=()) -> Brain:
    """A Brain whose model is fake: scripted replies, no API calls, no cost."""
    model = FakeToolCallingModel(messages=iter(replies))
    agent = create_agent(model, tools=list(tools), name="jarvis", checkpointer=InMemorySaver())
    return Brain(agent)


@tool
def check_sky() -> dict:
    """Look at the sky."""
    return {"sky": "grey"}


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
    state = await brain.agent.aget_state(brain.config())
    assert len(state.values["messages"]) == 4  # you, Jarvis, you, Jarvis


async def test_user_message_carries_the_time():
    brain = make_brain("Noted.")
    await collect(brain, "what time is it?")
    state = await brain.agent.aget_state(brain.config())
    assert state.values["messages"][0].content.startswith("[")


async def test_tool_results_are_not_spoken():
    brain = make_brain(
        AIMessage("Let me look.", tool_calls=[ToolCall(name="check_sky", args={}, id="call_1")]),
        "Grey, sir.",
        tools=[check_sky],
    )
    reply = "".join(await collect(brain, "how's the sky?"))
    assert reply == "Let me look. Grey, sir."  # a space between the two model steps, no tool JSON


def test_timestamp_format():
    now = datetime(2026, 10, 3, 14, 5, tzinfo=ZoneInfo("America/Vancouver"))
    assert timestamped("hi", now) == "[Saturday 3 October 2026, 2:05 PM PDT] hi"
