"""The Brain: the single doorway into Jarvis's mind.

Text chat calls it today; the voice agent will call it in Phase 7. Callers send text in and get
Jarvis's reply back as a stream of text chunks; they never touch LangGraph directly.
"""

from collections.abc import AsyncIterator
from datetime import datetime

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph


def timestamped(text: str, now: datetime) -> str:
    """Prefix the user's words with the local time, so the system prompt never has to change."""
    label = now.strftime("%A %-d %B %Y, %-I:%M %p")
    if now.tzname():
        label += f" {now.tzname()}"
    return f"[{label}] {text}"


class Brain:
    def __init__(self, agent: CompiledStateGraph, thread_id: str) -> None:
        self.agent = agent
        self.config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    async def turn(self, text: str) -> AsyncIterator[str]:
        """Send one message to Jarvis and stream his reply back, chunk by chunk."""
        message = HumanMessage(timestamped(text, datetime.now().astimezone()))
        speaking_step = None  # which loop step Jarvis's current words come from
        async for chunk in self.agent.astream(
            {"messages": [message]}, self.config, stream_mode="messages", version="v2"
        ):
            token, metadata = chunk["data"]
            # Only the model's own words: skips tool results and empty tool-call chunks.
            if metadata.get("langgraph_node") == "model" and token.text:
                step = metadata.get("langgraph_step")
                if speaking_step is not None and step != speaking_step:
                    yield " "  # he spoke, used a tool, now speaks again: keep sentences apart
                speaking_step = step
                yield token.text
