import asyncio

import pytest
from fakes import FakeFirecrawl

from jarvis.agents import registry
from jarvis.agents.registry import SUB_AGENTS, Health, SubAgent, agent_tools, roll_call
from jarvis.agents.search import NOT_SET_UP, check_search
from jarvis.config import Settings
from jarvis.tools.web_search import firecrawl_status

NO_KEYS = Settings(_env_file=None)


def sub_agent(name: str, check) -> SubAgent:
    return SubAgent(name, make_tool=lambda settings: None, check=check)


async def ready(settings) -> str:
    return "Ready."


async def broken(settings) -> str:
    raise RuntimeError("Service down.")


async def asleep(settings) -> str:
    await asyncio.sleep(10)
    return "Ready, eventually."


async def test_roll_call_reports_each_agent(monkeypatch):
    monkeypatch.setattr(registry, "CHECK_TIMEOUT_SECONDS", 0.01)
    agents = [sub_agent("calm", ready), sub_agent("broken", broken), sub_agent("slow", asleep)]
    assert await roll_call(NO_KEYS, agents) == {
        "calm": Health(True, "Ready."),
        "broken": Health(False, "Service down."),  # one failure doesn't stop the others
        "slow": Health(False, "No answer within 0.01 seconds."),
    }


def test_every_sub_agent_becomes_a_tool_for_jarvis():
    names = [tool.name for tool in agent_tools(NO_KEYS)]
    assert names == [f"{agent.name}_agent" for agent in SUB_AGENTS]  # e.g. "search_agent"


async def test_search_is_absent_without_a_key():
    with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
        await check_search(NO_KEYS)
    assert (await roll_call(NO_KEYS))["search"] == Health(False, NOT_SET_UP)


async def test_firecrawl_status_reports_credits():
    assert await firecrawl_status(FakeFirecrawl(credits=480)) == (
        "Firecrawl ready, 480 credits left."
    )
    with pytest.raises(RuntimeError, match="out of credits"):
        await firecrawl_status(FakeFirecrawl(credits=0))
