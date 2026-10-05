"""The Brain: the single doorway into Jarvis's mind.

Text chat calls it today; the voice agent will call it in Phase 7. Callers send text in and get
Jarvis's reply back as a stream of text chunks; they never touch LangGraph directly.
Events from tools (e.g. "the search agent started") arrive on `brain.events` as they happen.
When a change needs your OK, the reply ends with a "Shall I…?", and your next message is read
as the answer (see `approvals.py`).
"""

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt

from jarvis.brain.approvals import Approvals, Question, descriptions
from jarvis.brain.memory import daily_thread_id
from jarvis.events import Event, EventBus


def timestamped(text: str, now: datetime) -> str:
    """Prefix the user's words with the local time, so the system prompt never has to change."""
    label = now.strftime("%A %-d %B %Y, %-I:%M %p")
    if now.tzname():
        label += f" {now.tzname()}"
    return f"[{label}] {text}"


class Brain:
    def __init__(
        self,
        agent: CompiledStateGraph,
        new_day_hour: int = 4,
        approvals: Approvals | None = None,
    ) -> None:
        self.agent = agent
        self.new_day_hour = new_day_hour
        self.approvals = approvals  # asks "Shall I…?" before any change and reads your answer
        self.events = EventBus()  # chat (and later voice) subscribe to show what's happening
        self.question: Question | None = None  # a "Shall I…?" waiting for your answer

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
        graph_input, config = await self.read_message(text, now)
        async for chunk in self.run(graph_input, config, now):
            yield chunk

    async def read_message(self, text: str, now: datetime) -> tuple[Any, RunnableConfig]:
        """Your message as an answer to Jarvis's open question, or else as a new message."""
        question, self.question = self.question, None  # each question takes one answer at most
        if question and question.still_open(now) and self.approvals:
            reading = await self.approvals.read(question.text, text)
            if reading.decision != "unrelated":
                return Command(resume=question.resume_with(reading)), question.config
        # A new message. Any change still waiting is dropped by LangGraph, unsaved.
        return {"messages": [HumanMessage(timestamped(text, now))]}, self.config(now)

    async def run(
        self, graph_input: Any, config: RunnableConfig, now: datetime
    ) -> AsyncIterator[str]:
        """Stream Jarvis's words; if a change is waiting for your OK, end by asking for it."""
        speaking_step = None  # which loop step Jarvis's current words come from
        paused: list[Interrupt] = []  # changes waiting for your OK
        async for chunk in self.agent.astream(
            graph_input,
            config,
            # His words + events tools write + each step's update (where pauses show up).
            stream_mode=["messages", "custom", "updates"],
            version="v2",
        ):
            if chunk["type"] == "updates":
                paused += chunk["data"].get("__interrupt__", ())
                continue
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
        if paused:
            if speaking_step is not None:
                yield " "
            async for words in self.ask(paused, config, now):
                yield words

    async def ask(
        self, paused: Sequence[Interrupt], config: RunnableConfig, now: datetime
    ) -> AsyncIterator[str]:
        """Ask "Shall I…?" about the paused changes, and wait for your answer."""
        if self.approvals is None:  # a wiring bug: every Brain with write tools needs Approvals
            raise RuntimeError("A change is waiting for approval, but Brain has no Approvals.")
        question = []
        async for words in self.approvals.ask(paused, timestamped("Waiting for approval:", now)):
            question.append(words)
            yield words
        # Set only after the last word is out: a question cut off mid-way can't be answered.
        self.question = Question(
            tuple(paused), "".join(question), datetime.now().astimezone(), config
        )
        # The exact change, in code's words: a spoken question may sum up ("those 11 pages").
        self.events.publish(Event("approval_needed", {"changes": descriptions(paused)}))
