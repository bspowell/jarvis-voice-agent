"""The Search agent: Jarvis's web researcher, a sub-agent wrapped as one tool.

Jarvis calls `search_agent` like any other tool. Behind it, a second agent with its own model,
prompt and tools searches the web, reads pages, and reports back in a few sentences. The pages
stay in the Search agent's context; only its short report joins Jarvis's conversation, so his
daily thread stays small, fast and cheap.
"""

from datetime import datetime
from typing import Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware, ToolErrorMiddleware
from langchain.tools import BaseTool, ToolRuntime, tool
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from jarvis.brain.brain import timestamped
from jarvis.brain.context import JarvisContext
from jarvis.brain.models import make_model
from jarvis.config import Settings
from jarvis.events import Event
from jarvis.prompts import load_prompt
from jarvis.tools.errors import explain_tool_error
from jarvis.tools.web_search import firecrawl_client, firecrawl_status, read_page, web_search

# A budget per request: a deep search needs about four tool calls. Without one, a confused
# agent could keep searching (and spending credits): LangGraph allows 10,007 steps by default.
MAX_TOOL_CALLS = 6
# Repeated at the end of each request: format rules stick better there than in the system
# prompt (measured: deep reports ~40% shorter, ~2 s sooner).
REPORT_WORDS = {"quick": 80, "deep": 150}
NOT_SET_UP = "Web search isn't set up: FIRECRAWL_API_KEY is missing from .env."


def build_search_agent(model: str) -> CompiledStateGraph:
    return create_agent(
        model=make_model(model),
        tools=[web_search, read_page],
        system_prompt=load_prompt("search_agent"),
        middleware=[
            # Past the budget, tool calls are refused and the agent must report what it has.
            ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS),
            ToolErrorMiddleware(explain_tool_error),  # service trouble becomes a note
        ],
        # Stateless: every request starts fresh and nothing is saved. Left at the default, each
        # step (web pages included) would be saved inside Jarvis's conversation in jarvis.db.
        checkpointer=False,
        name="search_agent",
    )


def make_search_tool(settings: Settings) -> BaseTool:
    """Wrap the Search agent as the `search_agent` tool that Jarvis calls."""
    # Same agent, two models: quick lookups need speed; digging deeper needs judgment.
    agents = {
        "quick": build_search_agent(settings.models.search),
        "deep": build_search_agent(settings.models.search_deep),
    }

    @tool(parse_docstring=True)
    async def search_agent(
        request: str, runtime: ToolRuntime[JarvisContext], depth: Literal["quick", "deep"] = "quick"
    ) -> str:
        """Ask your Search agent to look something up on the web; it reports back briefly.

        Args:
            request: What to find out, as a full question, e.g. "What's the newest LangGraph?"
            depth: "quick" for most questions; "deep" only when the user asks you to dig deeper
                or research something.
        """
        if settings.firecrawl_api_key is None:
            return NOT_SET_UP
        runtime.stream_writer(Event("agent_started", {"agent": "search"}))  # cue the filler line
        task = timestamped(
            f"{request}\nDepth: {depth}. Report in one plain paragraph, at most "
            f"{REPORT_WORDS[depth]} words.",
            datetime.now().astimezone(),
        )
        result = await agents[depth].ainvoke({"messages": [HumanMessage(task)]})
        return result["messages"][-1].text  # only the report, never the pages it read

    return search_agent


async def check_search(settings: Settings) -> str:
    """Health check: a status line if the Search agent can work; raises with the reason if not."""
    if settings.firecrawl_api_key is None:
        raise RuntimeError(NOT_SET_UP)
    return await firecrawl_status(firecrawl_client())
