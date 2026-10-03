import asyncio
import itertools
from datetime import datetime

import pytest
from fakes import FakeToolCallingModel
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage

from jarvis.brain.brain import Brain
from jarvis.brain.memory import (
    INTERRUPTED,
    RepairInterruptedToolCalls,
    daily_thread_id,
    open_memory,
    summarize_long_days,
)


def brain_on(memory, *replies: str | AIMessage, tools=(), middleware=()) -> Brain:
    """A Brain with a scripted model, saving to a real (temporary) memory file."""
    model = FakeToolCallingModel(messages=iter(replies))
    agent = create_agent(
        model,
        tools=list(tools),
        middleware=list(middleware),
        checkpointer=memory.checkpointer,
        store=memory.store,
    )
    return Brain(agent)


async def collect(brain: Brain, text: str) -> list[str]:
    return [chunk async for chunk in brain.turn(text)]


async def saved_messages(brain: Brain) -> list:
    return (await brain.agent.aget_state(brain.config())).values["messages"]


# --- Which conversation is "today's" ---
@pytest.mark.parametrize(
    ("now", "thread"),
    [
        (datetime(2026, 10, 3, 9, 0), "jarvis-2026-10-03"),
        (datetime(2026, 10, 3, 23, 59), "jarvis-2026-10-03"),
        (datetime(2026, 10, 4, 1, 30), "jarvis-2026-10-03"),  # past midnight: still the same chat
        (datetime(2026, 10, 4, 4, 0), "jarvis-2026-10-04"),  # 4 AM: a new day begins
    ],
)
def test_one_conversation_per_day(now, thread):
    assert daily_thread_id(now, new_day_hour=4) == thread


# --- Surviving restarts ---
async def test_a_restart_keeps_todays_conversation(tmp_path):
    path = tmp_path / "jarvis.db"
    memory = await open_memory(path)
    await collect(brain_on(memory, "Noted, sir."), "My name is Sean.")
    await memory.close()  # Jarvis quits…

    memory = await open_memory(path)  # …and starts again, with a brand-new Brain
    brain = brain_on(memory, "You are Sean, sir.")
    assert await brain.messages_today() == 2
    await collect(brain, "What's my name?")
    first, *_ = await saved_messages(brain)
    assert first.text.endswith("My name is Sean.")
    assert await brain.messages_today() == 4  # you, Jarvis, you, Jarvis
    await memory.close()


async def test_memory_file_is_ready_for_two_processes(memory):
    """WAL: reading never blocks writing. Busy timeout: a second writer waits, not fails."""
    for db in (memory.checkpointer.conn, memory.store.conn):
        async with db.execute("PRAGMA journal_mode") as cursor:
            assert (await cursor.fetchone())[0] == "wal"
        async with db.execute("PRAGMA busy_timeout") as cursor:
            assert (await cursor.fetchone())[0] == 5000


# --- Repairing a turn cut off mid-tool ---
async def test_turn_cut_off_mid_tool_does_not_break_the_day(memory):
    started = asyncio.Event()

    @tool
    async def slow_lookup() -> str:
        """Takes a while."""
        started.set()
        await asyncio.sleep(10)
        return "never arrives"

    brain = brain_on(
        memory,
        AIMessage("", tool_calls=[ToolCall(name="slow_lookup", args={}, id="call_1")]),
        "At your service, sir.",
        tools=[slow_lookup],
        middleware=[RepairInterruptedToolCalls()],
    )
    turn = asyncio.create_task(collect(brain, "Look something up."))
    await started.wait()
    turn.cancel()  # Ctrl+C while the tool runs: its result never gets saved
    with pytest.raises(asyncio.CancelledError):
        await turn

    assert "".join(await collect(brain, "Never mind.")) == "At your service, sir."
    _you, tool_call, cancelled, _you_again, _answer = await saved_messages(brain)
    assert tool_call.tool_calls[0]["id"] == "call_1"
    assert isinstance(cancelled, ToolMessage)  # placed right after its call, as the API requires
    assert (cancelled.tool_call_id, cancelled.text) == ("call_1", INTERRUPTED)


def test_complete_history_is_left_alone():
    messages = [HumanMessage("Hello", id="1"), AIMessage("Good evening, sir.", id="2")]
    assert RepairInterruptedToolCalls().before_agent({"messages": messages}, runtime=None) is None


# --- Summarizing very long days ---
async def test_long_days_get_summarized(memory):
    summarizer = FakeToolCallingModel(messages=itertools.repeat("They discussed the weather."))
    brain = brain_on(
        memory,
        *["Indeed, sir."] * 3,
        middleware=[summarize_long_days(summarizer, after_tokens=60, keep_messages=4)],
    )
    replies = ["".join(await collect(brain, f"Remark {n} about the weather.")) for n in range(3)]

    assert replies == ["Indeed, sir."] * 3  # the summary is never spoken
    summary, *recent = await saved_messages(brain)
    assert summary.text.endswith("They discussed the weather.")
    assert len(recent) == 4 + 1  # the 4 kept messages + this turn's reply
    assert not any("Remark 0" in message.text for message in recent)  # condensed away
