from fakes import (
    BLOG,
    LANGGRAPH_PAGES,
    LANGGRAPH_RESULTS,
    PYPI,
    RELEASES,
    FakeFirecrawl,
    FakeToolCallingModel,
)
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.agents import search
from jarvis.agents.search import MAX_TOOL_CALLS, NOT_SET_UP, make_search_tool
from jarvis.brain.brain import Brain
from jarvis.config import Settings
from jarvis.events import Event
from jarvis.tools import web_search

ASK_SEARCH_AGENT = AIMessage(
    "",
    tool_calls=[
        ToolCall(name="search_agent", args={"request": "Latest LangGraph release?"}, id="j1")
    ],
)
SEARCH = AIMessage(
    "", tool_calls=[ToolCall(name="web_search", args={"query": "LangGraph release"}, id="s1")]
)
REPORT = "LangGraph 1.2.12 came out on 30 September 2026. Sources: PyPI."
# The Search agent's final message: the report, then its picks for reading more (code removes
# that line before Jarvis sees the report).
ANSWER = AIMessage(f"{REPORT}\nFurther reading: {PYPI}")


def search_agent_tool(monkeypatch, *replies, firecrawl=None, api_key="fc-test"):
    """The real `search_agent` tool: a scripted Search agent model and a fake Firecrawl."""
    model = FakeToolCallingModel(messages=iter(replies))
    monkeypatch.setattr(search, "make_model", lambda name: model)
    monkeypatch.setattr(web_search, "firecrawl_client", lambda: firecrawl)
    return make_search_tool(Settings(_env_file=None, firecrawl_api_key=api_key)), model


def jarvis_with(tool, *replies, checkpointer=None):
    """A stand-in Jarvis: scripted replies, and the search tool as his only tool."""
    model = FakeToolCallingModel(messages=iter(replies))
    return create_agent(model, tools=[tool], checkpointer=checkpointer, name="jarvis")


async def ask(agent, text: str) -> list:
    config = {"configurable": {"thread_id": "test"}}
    return (await agent.ainvoke({"messages": [("user", text)]}, config))["messages"]


async def test_jarvis_hears_only_the_short_report(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    tool, _ = search_agent_tool(monkeypatch, SEARCH, ANSWER, firecrawl=firecrawl)
    jarvis = jarvis_with(tool, ASK_SEARCH_AGENT, AIMessage("1.2.12, sir."))

    _you, _delegation, report, answer = await ask(jarvis, "What's the latest LangGraph?")
    assert isinstance(report, ToolMessage)
    assert report.text == REPORT
    assert answer.text == "1.2.12, sir."
    assert firecrawl.searches == ["LangGraph release"]
    # The pages the Search agent read never enter Jarvis's conversation.
    assert "Fixes a checkpoint bug" not in str([report, answer])


async def test_search_agent_gets_only_the_task_with_time_and_depth(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    tool, model = search_agent_tool(monkeypatch, SEARCH, ANSWER, firecrawl=firecrawl)
    jarvis = jarvis_with(tool, ASK_SEARCH_AGENT, AIMessage("1.2.12, sir."))
    await ask(jarvis, "My name is Sean. What's the latest LangGraph?")

    first_call = model.seen[0]
    task = first_call[-1].text
    assert task.startswith("[")  # the current time, like Jarvis's own messages
    assert "Latest LangGraph release?\nDepth: quick." in task
    # The format rules, repeated where they stick best: the end of the request.
    assert "at most 80 words, then a last line: Further reading" in task
    assert "Sean" not in str(first_call)  # isolated: none of Jarvis's conversation


async def test_search_agent_saves_nothing_in_jarvis_memory(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    tool, _ = search_agent_tool(monkeypatch, SEARCH, ANSWER, firecrawl=firecrawl)
    saver = InMemorySaver()
    jarvis = jarvis_with(tool, ASK_SEARCH_AGENT, AIMessage("1.2.12, sir."), checkpointer=saver)
    await ask(jarvis, "What's the latest LangGraph?")

    saved_conversations = {c.config["configurable"]["checkpoint_ns"] for c in saver.list(None)}
    assert saved_conversations == {""}  # only Jarvis's own; no nested search-agent checkpoints


async def test_a_runaway_search_agent_is_stopped(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    keeps_searching = [
        AIMessage("", tool_calls=[ToolCall(name="web_search", args={"query": "x"}, id=f"s{n}")])
        for n in range(MAX_TOOL_CALLS + 3)
    ]
    tool, _ = search_agent_tool(monkeypatch, *keeps_searching, ANSWER, firecrawl=firecrawl)
    jarvis = jarvis_with(tool, ASK_SEARCH_AGENT, AIMessage("1.2.12, sir."))

    *_, report, _answer = await ask(jarvis, "What's the latest LangGraph?")
    assert len(firecrawl.searches) == MAX_TOOL_CALLS  # the extra searches were refused
    assert report.text == REPORT  # …and the agent still reported back


async def test_missing_key_is_a_fact_for_jarvis(monkeypatch):
    firecrawl = FakeFirecrawl()
    tool, _ = search_agent_tool(monkeypatch, firecrawl=firecrawl, api_key=None)
    jarvis = jarvis_with(tool, ASK_SEARCH_AGENT, AIMessage("Alas, sir."))

    _you, _delegation, report, _answer = await ask(jarvis, "What's the latest LangGraph?")
    assert report.text == NOT_SET_UP
    assert firecrawl.searches == []


async def test_starting_a_search_cues_the_filler_but_its_words_stay_unspoken(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    tool, _ = search_agent_tool(monkeypatch, SEARCH, ANSWER, firecrawl=firecrawl)
    brain = Brain(jarvis_with(tool, ASK_SEARCH_AGENT, "1.2.12, sir.", checkpointer=InMemorySaver()))
    events = []
    brain.events.subscribe(events.append)

    reply = "".join([chunk async for chunk in brain.turn("What's the latest LangGraph?")])
    assert events == [
        Event("agent_started", {"agent": "search"}),
        Event("further_reading", {"sources": [{"title": "langgraph · PyPI", "url": PYPI}]}),
    ]
    assert reply == "1.2.12, sir."  # Jarvis's words only: not the report, not the pages


async def test_further_reading_keeps_only_pages_from_the_results(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    picks = [f"{BLOG}#latest", "https://made.up/page", PYPI, RELEASES, BLOG]
    answer = AIMessage(f"{REPORT}\n\n**Further reading:**\n" + "\n".join(f"- {p}" for p in picks))
    tool, _ = search_agent_tool(monkeypatch, SEARCH, answer, firecrawl=firecrawl)
    brain = Brain(jarvis_with(tool, ASK_SEARCH_AGENT, "1.2.12, sir.", checkpointer=InMemorySaver()))
    events = []
    brain.events.subscribe(events.append)

    [_chunk async for _chunk in brain.turn("What's the latest LangGraph?")]
    messages = (await brain.agent.aget_state(brain.config())).values["messages"]
    assert messages[2].text == REPORT  # Jarvis's copy: no addresses to read aloud
    links = events[-1].details["sources"]
    # A made-up address never reaches the screen; titles come from the results, not the model;
    # the same page twice is shown once; three at most, in the agent's order.
    assert links == [
        {"title": "LangChain Blog", "url": BLOG},
        {"title": "langgraph · PyPI", "url": PYPI},
        {"title": "Releases · langgraph", "url": RELEASES},
    ]


async def test_digging_deeper_uses_the_stronger_model(monkeypatch):
    settings = Settings(_env_file=None, firecrawl_api_key="fc-test")
    quick, deep = (FakeToolCallingModel(messages=iter([ANSWER])) for _ in range(2))
    models = {settings.models.search: quick, settings.models.search_deep: deep}
    monkeypatch.setattr(search, "make_model", lambda name: models[name])
    asks_to_dig = AIMessage(
        "",
        tool_calls=[
            ToolCall(
                name="search_agent",
                args={"request": "What's new in LangGraph?", "depth": "deep"},
                id="j1",
            )
        ],
    )
    jarvis = jarvis_with(make_search_tool(settings), asks_to_dig, AIMessage("Done, sir."))

    await ask(jarvis, "Dig deeper into what's new in LangGraph.")
    assert "Depth: deep." in deep.seen[0][-1].text
    assert quick.seen == []


async def test_no_picks_no_links(monkeypatch):
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    tool, _ = search_agent_tool(monkeypatch, SEARCH, AIMessage(REPORT), firecrawl=firecrawl)
    brain = Brain(jarvis_with(tool, ASK_SEARCH_AGENT, "1.2.12, sir.", checkpointer=InMemorySaver()))
    events = []
    brain.events.subscribe(events.append)

    [_chunk async for _chunk in brain.turn("What's the latest LangGraph?")]
    assert [event.name for event in events] == ["agent_started"]  # nothing recommended: no card
