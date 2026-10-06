"""The Notes agent: Jarvis's archivist, a sub-agent wrapped as one tool.

It works in two places: your Notion workspace (through Notion's MCP server) and your files (four
folders, sandboxed). Like Calendar, every write pauses for your OK. Unlike Calendar, it still
works without its online account: files don't need Notion, and only a request that needs Notion
shows the Connect Notion button.

Notion's tools come from its server, so the agent is assembled per request: connect (~0.2 s),
pick the tools, build, run, disconnect. A paused write survives that: on resume, the same tools
make the same graph, and LangGraph carries on from the saved checkpoint.
"""

import json
from collections.abc import Sequence
from datetime import datetime

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain.tools import BaseTool, ToolRuntime, tool
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from jarvis.agents.writes import needs_approval, with_receipts
from jarvis.auth.notion import is_signed_in
from jarvis.brain.brain import timestamped
from jarvis.brain.context import JarvisContext
from jarvis.brain.models import make_model
from jarvis.config import Settings
from jarvis.events import Event
from jarvis.prompts import load_prompt
from jarvis.tools.errors import explain_tool_error
from jarvis.tools.files import (
    Sandbox,
    add_to_file,
    create_file,
    describe_file_addition,
    describe_new_file,
    find_files,
    list_folder,
    read_file,
)
from jarvis.tools.notion import (
    READ_TOOLS,
    ShowNotionPages,
    ask_about_move,
    connect_notion,
    describe_move,
    describe_new_page,
    describe_page_addition,
    listed,
    notion_tools,
    quoted,
)

MAX_TOOL_CALLS = 8  # a busy request: search, read, propose, propose again after "under Health"…
FILE_TOOLS = [find_files, list_folder, read_file, create_file, add_to_file]
# Each tool that writes, with the words that say what it's about to do. No tool deletes.
WRITE_TOOLS = {
    create_file.name: needs_approval(describe_new_file),
    add_to_file.name: needs_approval(describe_file_addition),
    "create_notion_page": needs_approval(describe_new_page),
    "add_to_notion_page": needs_approval(describe_page_addition),
    "move_notion_pages": needs_approval(describe_move, when=ask_about_move),
}


def build_notes_agent(settings: Settings, notion: Sequence[BaseTool] | None) -> CompiledStateGraph:
    """The Notes agent for one request: the file tools, plus Notion's (or `connect_notion`)."""
    middleware: list[AgentMiddleware] = [
        # Nothing is written until you say yes: the run pauses before every write.
        HumanInTheLoopMiddleware(interrupt_on=WRITE_TOOLS),
        ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS),
        # Within a request, each model call reuses the tools and prompt from the first one.
        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        ToolErrorMiddleware(explain_tool_error),  # service trouble becomes a note
    ]
    if settings.notion.open_pages:
        middleware.append(ShowNotionPages())  # each page read or written opens in Notion
    return create_agent(
        model=make_model(settings.models.notes),
        tools=[*FILE_TOOLS, *(notion if notion is not None else [connect_notion])],
        system_prompt=load_prompt("notes_agent"),
        middleware=middleware,
        # No `checkpointer=`: the default lets a paused write resume (as in calendar.py).
        name="notes_agent",
    )


def make_notes_tool(settings: Settings) -> BaseTool:
    """Wrap the Notes agent as the `notes_agent` tool that Jarvis calls."""

    @tool(parse_docstring=True)
    async def notes_agent(request: str, runtime: ToolRuntime[JarvisContext]) -> str:
        """Ask your Notes agent about the user's notes, in Notion or in files on their Mac:
        finding and reading them, writing new ones, adding to them, or moving Notion pages
        under other pages. It reports back briefly.

        Args:
            request: The user's request in full, with names, places and wording as they said
                them, e.g. "Add 'call the plumber' to my to-do list in Notion".
        """
        runtime.stream_writer(Event("agent_started", {"agent": "notes"}))  # cue the filler line
        task = timestamped(request, datetime.now().astimezone())
        # After you answer "Shall I…?", this tool runs again from the top: it reconnects, builds
        # the same agent, and LangGraph resumes the paused run from its saved state.
        async with notion_tools(settings) as notion:
            agent = build_notes_agent(settings, notion)
            result = await agent.ainvoke({"messages": [HumanMessage(task)]})
        if called(result["messages"], connect_notion.name):
            runtime.stream_writer(Event("sign_in_needed", {"service": "notion"}))  # the button
        # The report, plus facts Jarvis can trust: what you changed, and what was saved.
        return with_receipts(result["messages"], receipt_in, "Saved")

    return notes_agent


def called(messages: Sequence[AnyMessage], tool_name: str) -> bool:
    return any(isinstance(m, ToolMessage) and m.name == tool_name for m in messages)


def receipt_in(message: AnyMessage) -> str | None:
    """ "created the Notion page “Dentist” in your private pages", from a write's result."""
    if not isinstance(message, ToolMessage) or message.name not in WRITE_TOOLS:
        return None
    try:
        result = json.loads(message.text)
    except ValueError:
        return None
    if not isinstance(result, dict):
        return None
    created, added, moved = result.get("created"), result.get("added_to"), result.get("moved")
    if isinstance(moved, dict):
        pages = [quoted(title, "a page") for title in moved["titles"]]
        return f"moved {listed(pages)} {moved['where']}"
    if isinstance(created, dict):
        return f"created the Notion page “{created['title']}” {created['where']}"
    if isinstance(added, dict):
        return f"added to the Notion page “{added['title']}”"
    if created:
        return f"created the file {created}"
    if added:
        return f"added to the file {added}"
    return None


async def check_notes(settings: Settings) -> str:
    """Health check: a status line if the Notes agent can work; raises with the reason if not.

    Files always work; Notion counts when it's connected.
    """
    folders = [folder for folder in Sandbox.of(settings.files.folders).folders if folder.is_dir()]
    if not folders:
        raise RuntimeError("None of the folders in jarvis.toml ([files] folders) exist.")
    files = f"files in {len(folders)} folders"
    if not is_signed_in(settings):
        return f"Ready: {files}. Notion not connected yet: ask about your notes to connect."
    async with notion_tools(settings) as notion:
        if notion is None:
            return f"Ready: {files}. Notion's sign-in stopped working: ask about your notes."
        missing = set(READ_TOOLS) - {notion_tool.name for notion_tool in notion}
        if missing:
            return f"Ready: {files}. Notion no longer offers {', '.join(sorted(missing))}."
    return f"Ready: Notion and {files}."
