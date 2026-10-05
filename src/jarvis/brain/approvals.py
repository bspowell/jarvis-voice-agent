"""Approvals: nothing is written until you say so. The model interprets; code owns the state.

A sub-agent about to add or change something pauses the whole run, and the pause reaches
`Brain` as a LangGraph interrupt. Then:
1. Ask: a small, fast model turns the exact description of the change into one spoken question.
2. Read: your next message goes to the same model, which sorts it into approve / reject /
   change / unrelated. No keyword lists: "go for it" and "yes, but at 1" both work.
3. Resume: code turns that reading into the decision for the paused run, addressed to that
   interrupt's id. Anything unrelated, or an answer after five minutes, is treated as a new
   message instead: the paused change is dropped, unsaved.
"""

import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Interrupt
from pydantic import BaseModel, Field

from jarvis.prompts import load_prompt

ANSWER_WITHIN = timedelta(minutes=5)  # a later answer starts afresh: nothing stale gets approved
CHANGE_FIRST = "The user wants a change first: "  # how a change reaches the agent (see below)
SAID_NO = "The user said no."  # how a plain no reaches the agent


class Reading(BaseModel):
    """What your reply to "Shall I…?" means."""

    decision: Literal["approve", "reject", "change", "unrelated"]
    change: str | None = Field(
        default=None,
        description='Only for change: one clear instruction, e.g. "start at 1 PM instead of noon".',
    )


@dataclass(frozen=True)
class Question:
    """A "Shall I…?" Jarvis asked and is waiting on."""

    interrupts: tuple[Interrupt, ...]  # the paused changes
    text: str  # the question, as you heard it
    asked_at: datetime
    config: RunnableConfig  # the conversation it belongs to

    def still_open(self, now: datetime) -> bool:
        return now - self.asked_at <= ANSWER_WITHIN

    def resume_with(self, reading: Reading) -> dict[str, Any]:
        """Your answer for each paused change, addressed by its interrupt id (so nowhere else)."""
        decision = decision_for(reading)
        return {
            interrupt.id: {"decisions": [decision] * len(interrupt.value["action_requests"])}
            for interrupt in self.interrupts
        }


def decision_for(reading: Reading) -> dict[str, str]:
    """The decision `HumanInTheLoopMiddleware` expects.

    A change is a "reject" with a reason: the agent proposes again and you're asked again, so a
    changed version is never saved without you hearing it first.
    """
    if reading.decision == "approve":
        return {"type": "approve"}
    if reading.decision == "change":
        return {
            "type": "reject",
            "message": f"{CHANGE_FIRST}“{reading.change}”. Propose it again with only that "
            "changed; they'll be asked again.",
        }
    return {"type": "reject", "message": f"{SAID_NO} Don't retry; nothing was changed."}


def requested_change(text: str) -> str | None:
    """The change you asked for, if `text` (a rejected tool call's result) carries one."""
    found = re.search(re.escape(CHANGE_FIRST) + "“(.*?)”", text)
    return found.group(1) if found else None


def said_no(text: str) -> bool:
    """True if `text` (a rejected tool call's result) is your plain no."""
    return SAID_NO in text


def descriptions(interrupts: Sequence[Interrupt]) -> list[str]:
    """What each paused change would do, in the exact words its tool's code wrote."""
    return [
        request["description"]
        for interrupt in interrupts
        for request in interrupt.value["action_requests"]
    ]


class Approvals:
    """Asks before changes and reads your answers, with one small, fast model."""

    def __init__(self, model: BaseChatModel) -> None:
        self.model = model
        # Structured output: the answer comes back as a Reading, never as free text to parse.
        self.reader = model.with_structured_output(Reading, method="json_schema")

    async def ask(self, interrupts: Sequence[Interrupt], header: str) -> AsyncIterator[str]:
        """Stream one spoken question asking you to approve the paused change(s).

        `header` carries the current time, so "tomorrow" is only said when it's true.
        """
        changes = "\n".join(f"- {text}" for text in descriptions(interrupts))
        messages = [
            SystemMessage(load_prompt("approval_question")),
            HumanMessage(f"{header}\n{changes}"),
        ]
        async for chunk in self.model.astream(messages):
            if chunk.text:
                yield chunk.text

    async def read(self, question: str, reply: str) -> Reading:
        """What your reply to the question means."""
        messages = [
            SystemMessage(load_prompt("approval_reply")),
            HumanMessage(f"Jarvis asked: {question}\nThe user replied: {reply}"),
        ]
        return await self.reader.ainvoke(messages)
