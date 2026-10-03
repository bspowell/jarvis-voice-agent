"""Jarvis's sub-agents in one list: the tool Jarvis calls to delegate, and a health check.

At boot, `roll_call` runs every health check at once. The greeting counts the agents that
answered ("One Agent present and accounted for"); `jarvis doctor` shows why any didn't.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from langchain.tools import BaseTool

from jarvis.agents.search import check_search, make_search_tool
from jarvis.config import Settings

CHECK_TIMEOUT_SECONDS = 5.0  # a check that takes longer counts as absent


@dataclass(frozen=True)
class SubAgent:
    name: str
    make_tool: Callable[[Settings], BaseTool]  # the tool Jarvis calls to delegate to it
    check: Callable[[Settings], Awaitable[str]]  # a status line, or raises if it can't work


SUB_AGENTS = (
    SubAgent("search", make_tool=make_search_tool, check=check_search),
    # Phase 5: calendar · Phase 6: notes
)


@dataclass(frozen=True)
class Health:
    present: bool
    detail: str  # e.g. "Firecrawl ready, 480 credits left." or why it's absent


def agent_tools(settings: Settings) -> list[BaseTool]:
    return [agent.make_tool(settings) for agent in SUB_AGENTS]


async def roll_call(
    settings: Settings, agents: Sequence[SubAgent] = SUB_AGENTS
) -> dict[str, Health]:
    """Check every sub-agent at once. A check that fails, crashes or is too slow means absent."""
    results = await asyncio.gather(
        *(asyncio.wait_for(agent.check(settings), CHECK_TIMEOUT_SECONDS) for agent in agents),
        return_exceptions=True,  # one broken check must not stop the others
    )
    return {agent.name: health(result) for agent, result in zip(agents, results, strict=True)}


def health(result: str | BaseException) -> Health:
    if isinstance(result, TimeoutError):
        return Health(False, f"No answer within {CHECK_TIMEOUT_SECONDS:g} seconds.")
    if isinstance(result, BaseException):
        return Health(False, str(result) or type(result).__name__)
    return Health(True, result)
