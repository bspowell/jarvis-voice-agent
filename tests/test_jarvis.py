import json

import httpx
import pytest
from fakes import FORECAST, FORECASTER, GEOCODER, PARIS_RESULTS, FakeToolCallingModel, fake_web
from langchain_core.messages import AIMessage, ToolCall, ToolMessage

from jarvis.brain import jarvis
from jarvis.brain.jarvis import build_jarvis
from jarvis.config import Settings
from jarvis.tools import weather
from jarvis.tools.memory_tools import list_facts

ASKS_FOR_PARIS = AIMessage(
    "", tool_calls=[ToolCall(name="get_weather", args={"place": "Paris"}, id="call_1")]
)


def jarvis_with_script(monkeypatch, *replies: AIMessage, store=None):
    """The real Jarvis (tools, middleware) with a scripted model instead of Claude."""
    model = FakeToolCallingModel(messages=iter(replies))
    monkeypatch.setattr(jarvis, "make_model", lambda name: model)
    return build_jarvis(Settings(_env_file=None), store=store)


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
