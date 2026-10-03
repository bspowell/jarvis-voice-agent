"""Jarvis's memory, kept in one SQLite file (`data/jarvis.db`).

- Checkpointer → today's conversation: every message, saved after each step of the loop.
- Store → long-term facts you asked him to remember, shared by every day's conversation.

Plus two middleware that keep a whole day's conversation healthy: one condenses very long days,
one repairs a turn that was cut off mid-tool.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
from langchain.agents.middleware import AgentMiddleware, AgentState, SummarizationMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime
from langgraph.store.sqlite import AsyncSqliteStore

from jarvis.prompts import load_prompt

DB_FILE = "jarvis.db"
BUSY_TIMEOUT_SECONDS = 5.0  # if voice and chat write at the same moment, wait instead of failing


def daily_thread_id(now: datetime, new_day_hour: int) -> str:
    """Today's conversation, e.g. "jarvis-2026-10-03". Same day → same thread, even after a restart.

    The day turns over at `new_day_hour` (4 AM), not midnight: a late-night chat stays one thread.
    """
    day = (now - timedelta(hours=new_day_hour)).date()
    return f"jarvis-{day.isoformat()}"


@dataclass
class Memory:
    """Both memories, open on the same SQLite file."""

    checkpointer: AsyncSqliteSaver  # today's conversation (short-term)
    store: AsyncSqliteStore  # facts you asked him to keep (long-term)

    async def close(self) -> None:
        """Always call this: an open connection keeps Python from exiting."""
        await self.store.conn.close()
        await self.checkpointer.conn.close()


async def open_memory(path: Path) -> Memory:
    """Open (or create) the memory file. Call `close()` when Jarvis shuts down."""
    path.parent.mkdir(parents=True, exist_ok=True)
    memory = Memory(
        AsyncSqliteSaver(await aiosqlite.connect(path, timeout=BUSY_TIMEOUT_SECONDS)),
        # The store manages its own transactions, so it needs a connection of its own.
        AsyncSqliteStore(
            await aiosqlite.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        ),
    )
    try:
        await memory.checkpointer.setup()  # creates tables, and WAL mode: reads never block writes
        await memory.store.setup()
    except BaseException:
        await memory.close()
        raise
    return memory


# --- Keeping a whole day's conversation healthy ---
SUMMARIZE_AFTER_TOKENS = 30_000  # a very long day; most days never get here
KEEP_RECENT_MESSAGES = 20


def summarize_long_days(
    model: BaseChatModel,
    after_tokens: int = SUMMARIZE_AFTER_TOKENS,
    keep_messages: int = KEEP_RECENT_MESSAGES,
) -> SummarizationMiddleware:
    """Safety net: past `after_tokens`, older messages become one summary; recent ones stay as-is.

    Keeps every call fast and cheap however long the day gets.
    """
    return SummarizationMiddleware(
        model=model,
        trigger=("tokens", after_tokens),
        keep=("messages", keep_messages),
        summary_prompt=load_prompt("summary"),
        # Summarize everything being replaced. The default only reads the last 4,000 tokens,
        # which would silently drop the start of the day, including any earlier summary.
        trim_tokens_to_summarize=None,
    )


INTERRUPTED = "Cancelled: the user interrupted before this finished."


class RepairInterruptedToolCalls(AgentMiddleware):
    """Repairs a saved conversation after a turn is cut off mid-tool.

    Ctrl+C (or "stop" in voice) can land after Jarvis asked for a tool but before it answered.
    The saved thread then holds a tool call with no result, and the API rejects every later
    request in that thread. At the start of each turn, this adds a "cancelled" result for it.
    """

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state["messages"]
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        repaired: list[AnyMessage] = []
        for message in messages:
            repaired.append(message)
            if isinstance(message, AIMessage):
                repaired += [
                    ToolMessage(
                        INTERRUPTED, tool_call_id=call["id"], name=call["name"], status="error"
                    )
                    for call in message.tool_calls
                    if call["id"] not in answered
                ]
        if len(repaired) == len(messages):
            return None  # nothing was cut off: leave the history untouched
        # Rewrite the list so each cancelled result sits right after its tool call.
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}
