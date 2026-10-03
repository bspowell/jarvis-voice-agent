"""The Brain: the single doorway into Jarvis's mind.

Text chat calls it today; the voice agent will call it in Phase 7. Callers send text in and get
Jarvis's reply back as a stream of text chunks; they never touch LangGraph directly.
Events from tools (e.g. "the search agent started") arrive on `brain.events` as they happen.
"""

from collections.abc import AsyncIterator
from datetime import datetime

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from jarvis.brain.memory import daily_thread_id
from jarvis.events import Event, EventBus


def timestamped(text: str, now: datetime) -> str:
    """Prefix the user's words with the local time, so the system prompt never has to change."""
    label = now.strftime("%A %-d %B %Y, %-I:%M %p")
    if now.tzname():
        label += f" {now.tzname()}"
    return f"[{label}] {text}"


class Brain:
    def __init__(self, agent: CompiledStateGraph, new_day_hour: int = 4) -> None:
        self.agent = agent
        self.new_day_hour = new_day_hour
        self.events = EventBus()  # chat (and later voice) subscribe to show what's happening

    def config(self, now: datetime | None = None) -> RunnableConfig:
        """Which conversation LangGraph should load and save: today's."""
        now = now or datetime.now().astimezone()
        return {"configurable": {"thread_id": daily_thread_id(now, self.new_day_hour)}}

    async def messages_today(self) -> int:
        """How many messages today's conversation already holds (0 on a fresh day)."""
        state = await self.agent.aget_state(self.config())
        return len(state.values.get("messages", []))

    async def turn(self, text: str) -> AsyncIterator[str]:
        """Send one message to Jarvis and stream his reply back, chunk by chunk."""
        now = datetime.now().astimezone()
        message = HumanMessage(timestamped(text, now))
        speaking_step = None  # which loop step Jarvis's current words come from
        async for chunk in self.agent.astream(
            {"messages": [message]},
            self.config(now),
            stream_mode=["messages", "custom"],  # his words + events tools write to the stream
            version="v2",
        ):
            if chunk["type"] == "custom":
                if isinstance(chunk["data"], Event):
                    self.events.publish(chunk["data"])
                continue
            token, metadata = chunk["data"]
            # Only the model's own words: skips tool results and empty tool-call chunks.
            if metadata.get("langgraph_node") == "model" and token.text:
                step = metadata.get("langgraph_step")
                if speaking_step is not None and step != speaking_step:
                    yield " "  # he spoke, used a tool, now speaks again: keep sentences apart
                speaking_step = step
                yield token.text
