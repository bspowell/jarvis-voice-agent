import json

import httpx
import pytest
from fakes import (
    FORECAST,
    FORECASTER,
    GEOCODER,
    LANGGRAPH_PAGES,
    LANGGRAPH_RESULTS,
    PARIS_RESULTS,
    FakeFirecrawl,
    FakeToolCallingModel,
    fake_web,
)
from langchain_core.messages import AIMessage, ToolCall, ToolMessage

from jarvis.agents import search
from jarvis.brain import jarvis
from jarvis.brain.jarvis import build_jarvis
from jarvis.config import Settings
from jarvis.tools import weather, web_search
from jarvis.tools.memory_tools import list_facts

ASKS_FOR_PARIS = AIMessage(
    "", tool_calls=[ToolCall(name="get_weather", args={"place": "Paris"}, id="call_1")]
)


def jarvis_with_script(
    monkeypatch, *replies: AIMessage, store=None, search_replies=(), firecrawl_api_key=None
):
    """The real Jarvis (tools, sub-agents, middleware) with scripted models instead of Claude."""
    model = FakeToolCallingModel(messages=iter(replies))
    monkeypatch.setattr(jarvis, "make_model", lambda name: model)
    search_model = FakeToolCallingModel(messages=iter(search_replies))
    monkeypatch.setattr(search, "make_model", lambda name: search_model)
    return build_jarvis(Settings(_env_file=None, firecrawl_api_key=firecrawl_api_key), store=store)


def web_answers(monkeypatch, answers: dict) -> None:
    monkeypatch.setattr(weather, "http_client", lambda: fake_web(answers))


async def ask(agent, text: str) -> list:
    result = await agent.ainvoke({"messages": [("user", text)]})
    return result["messages"]


async def test_jarvis_checks_the_weather(monkeypatch):
    web_answers(monkeypatch, {GEOCODER: PARIS_RESULTS, FORECASTER: FORECAST})
    agent = jarvis_with_script(monkeypatch, ASKS_FOR_PARIS, AIMessage("Drizzly, sir."))
    _you, tool_call, tool_result, answer = await ask(agent, "Weather in Paris?")
    assert tool_call.tool_calls[0]["name"] == "get_weather"
    assert isinstance(tool_result, ToolMessage)
    assert json.loads(tool_result.text)["place"] == "Paris, Île-de-France, France"
    assert answer.text == "Drizzly, sir."


async def test_offline_becomes_a_note_for_jarvis(monkeypatch):
    web_answers(monkeypatch, {GEOCODER: httpx.ConnectError("no internet")})
    agent = jarvis_with_script(monkeypatch, ASKS_FOR_PARIS, AIMessage("The heavens are silent."))
    *_, tool_result, answer = await ask(agent, "Weather in Paris?")
    assert tool_result.status == "error"
    assert "didn't answer" in tool_result.text
    assert answer.text == "The heavens are silent."


async def test_real_bugs_still_crash(monkeypatch):
    web_answers(monkeypatch, {GEOCODER: PARIS_RESULTS, FORECASTER: {"unexpected": "shape"}})
    agent = jarvis_with_script(monkeypatch, ASKS_FOR_PARIS)
    with pytest.raises(KeyError):  # a bug should be loud, not politely explained away
        await ask(agent, "Weather in Paris?")


async def test_jarvis_remembers_when_asked(monkeypatch, memory):
    fact = "The user's friend Ruth lives in Perth."
    remembers = AIMessage(
        "", tool_calls=[ToolCall(name="remember_fact", args={"fact": fact}, id="call_1")]
    )
    agent = jarvis_with_script(monkeypatch, remembers, AIMessage("Noted, sir."), store=memory.store)
    *_, answer = await ask(agent, "Remember that my friend Ruth lives in Perth.")
    assert answer.text == "Noted, sir."
    saved = await list_facts(memory.store)  # the tool reached the store through ToolRuntime
    assert saved["facts"][0]["fact"] == fact


async def test_jarvis_delegates_web_searches_to_his_search_agent(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    monkeypatch.setattr(web_search, "firecrawl_client", lambda: firecrawl)
    delegates = AIMessage(
        "",
        tool_calls=[
            ToolCall(name="search_agent", args={"request": "Latest LangGraph?"}, id="call_1")
        ],
    )
    searches = AIMessage(
        "", tool_calls=[ToolCall(name="web_search", args={"query": "LangGraph release"}, id="s1")]
    )
    report = "LangGraph 1.2.12 came out on 30 September 2026. Sources: PyPI."
    agent = jarvis_with_script(
        monkeypatch,
        delegates,
        AIMessage("Version 1.2.12, sir, barely a week old."),
        search_replies=[searches, AIMessage(report)],
        firecrawl_api_key="fc-test",
    )
    _you, delegation, tool_result, answer = await ask(agent, "What's the latest LangGraph?")
    assert delegation.tool_calls[0]["name"] == "search_agent"
    assert tool_result.text == report  # the Search agent's report, not the pages it read
    assert answer.text == "Version 1.2.12, sir, barely a week old."
    assert firecrawl.searches == ["LangGraph release"]
