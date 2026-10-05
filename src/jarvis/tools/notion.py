"""Notion tools for the Notes agent: Notion's own search and fetch, plus four careful tools of ours.

Notion's MCP server offers 44 tools. The Notes agent gets six:
- `notion-search` and `notion-fetch`: Notion's own, passed straight through by `MCPAdapter`.
- `list_database_pages`: ours. A database's own pages, never the pages nested inside them
  (Notion's search returns both, and a model can't reliably tell them apart).
- `create_notion_page`, `add_to_notion_page` and `move_notion_pages`: ours. They call Notion's
  create-pages, update-page and move-pages over the same connection, but can only create a page,
  add to the end of one, or move pages: never replace or remove what's written. Each pauses for
  your OK; `describe_*` say exactly what.

Each request opens its own connection (about 0.2 s), so renewed tokens and a fresh sign-in are
always picked up. `ShowNotionPages` opens each page read or written in the Notion app.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastmcp.client import Client
from langchain.agents.middleware import AgentMiddleware, AgentState, ToolCallRequest
from langchain.tools import BaseTool, ToolRuntime, tool
from langchain_core._api import suppress_langchain_beta_warning
from langchain_core.messages import AnyMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from mcp.types import TextContent

from jarvis.auth.notion import is_signed_in, notion_client, notion_store, stopped_working
from jarvis.config import Settings
from jarvis.tools.files import preview

# LangChain's MCP support is new ("beta"): chosen on purpose, so skip the warning it prints.
with suppress_langchain_beta_warning():
    from langchain.mcp import MCPAdapter

READ_TOOLS = ("notion-search", "notion-fetch")  # Notion's own tools, as they are
TITLE_SOURCES = {*READ_TOOLS, "list_database_pages"}  # results that name pages
HEX_RUN = re.compile(r"[0-9a-f-]{32,}")  # an id: 32 hex digits, dashes anywhere or nowhere
VIEW_URL = re.compile(r"view://[0-9a-f-]+")  # a database view, in a fetched database
MAX_ROWS = 300  # rows listed per database: three of Notion's pages of 100
MAX_MOVED = 25  # pages moved per approval: one slip can't flatten a whole workspace
UNSEEN_PAGE = "a page it didn't look up first"  # an id the agent never saw: say so, not a title
CONNECT_FIRST = (
    "Notion isn't connected, so it's out of reach. A “Connect Notion” button is showing for the "
    "user now. Once they say they've connected, ask me again."
)


@asynccontextmanager
async def notion_tools(settings: Settings) -> AsyncIterator[list[BaseTool] | None]:
    """Notion's tools for one request, over one connection. None if Notion isn't connected."""
    async with AsyncExitStack() as stack:
        yield await connect(settings, stack)


async def connect(settings: Settings, stack: AsyncExitStack) -> list[BaseTool] | None:
    if not is_signed_in(settings):
        return None
    adapter = MCPAdapter(notion_client(settings))
    try:
        await stack.enter_async_context(adapter)  # stays open until the request is done
    except RuntimeError as error:
        if not stopped_working(error):
            raise
        notion_store(settings).forget()  # the saved sign-in no longer works: offer the button
        return None
    offered = {mcp_tool.name: mcp_tool for mcp_tool in await adapter.list_tools()}
    notions_own = [offered[name] for name in READ_TOOLS if name in offered]
    return notions_own + reads_over(adapter.client) + writes_over(adapter.client)


@tool
def connect_notion() -> str:
    """Notion isn't connected yet. Call this when the request needs Notion: the user is shown a
    Connect Notion button."""
    return CONNECT_FIRST


# --- Our one read ---
def reads_over(client: Client) -> list[BaseTool]:
    """List a database's own pages, through this request's Notion connection."""

    @tool(parse_docstring=True)
    async def list_database_pages(database: str) -> dict[str, Any]:
        """List the pages in a Notion database (as its main view shows them): its rows, never
        the pages nested inside them.

        Args:
            database: The database's URL, as notion-fetch or notion-search showed it.
        """
        text, failed = await call(client, "notion-fetch", {"id": database})
        view = None if failed else VIEW_URL.search(text)
        if view is None:
            return {"error": "That isn't a database: pass the URL of the database itself."}
        rows: list[dict[str, Any]] = []
        query: dict[str, Any] = {"mode": "view", "view_url": view.group(), "page_size": 100}
        while len(rows) < MAX_ROWS:  # Notion sends 100 rows at a time
            text, failed = await call(client, "notion-query-data-sources", {"data": query})
            found = None if failed else json_object(text)
            if found is None:
                return {"error": text if failed else "Notion's answer couldn't be read."}
            rows += found.get("results", [])
            if not found.get("has_more"):
                break
            query = {**query, "start_cursor": found.get("next_cursor")}
        return {
            "pages": [
                {"id": page_id(row["url"]), "title": row_title(row), "url": row["url"]}
                for row in rows
                if row.get("url")
            ]
        }

    return [list_database_pages]


def row_title(row: dict[str, Any]) -> str:
    """A database row's title. Its column's name varies ("Name", "Title"…): take the first text."""
    texts = [value for key, value in row.items() if key != "url" and isinstance(value, str)]
    title = row.get("Name") if isinstance(row.get("Name"), str) else next(iter(texts), "")
    return title.strip() or "Untitled"


# --- Our three writes (they run only after the user approves) ---
def writes_over(client: Client) -> list[BaseTool]:
    """Create, add to and move pages, through this request's Notion connection."""

    @tool(parse_docstring=True)
    async def create_notion_page(
        title: str, content: str, runtime: ToolRuntime, parent: str | None = None
    ) -> dict[str, Any]:
        """Create a Notion page. The user confirms before it's created.

        Args:
            title: The page's title, e.g. "Dentist on Friday".
            content: What the page says, in simple Markdown: short paragraphs, "- " bullets,
                "- [ ] " to-dos.
            parent: The page to put it under: its id or URL, from notion-search or
                notion-fetch. Leave out to make it one of the user's private pages.
        """
        if parent and not page_id(parent):
            return {"error": "Find the parent page with notion-search, then pass its id or URL."}
        where = {"parent": {"page_id": page_id(parent)}} if parent else {"creation_mode": "draft"}
        page = {"properties": {"title": title}, "content": content}
        text, failed = await call(client, "notion-create-pages", {"pages": [page], **where})
        if failed:
            return {"error": text}
        place = under(runtime.state["messages"], parent) if parent else "in your private pages"
        return {"created": {"title": title, "where": place, "url": first_page_url(text)}}

    @tool(parse_docstring=True)
    async def add_to_notion_page(page: str, content: str, runtime: ToolRuntime) -> dict[str, Any]:
        """Add content to the end of a Notion page; nothing already there changes. The user
        confirms first.

        Args:
            page: The page's id or URL, from notion-search or notion-fetch.
            content: What to add, in simple Markdown, e.g. "- [ ] Call the plumber".
        """
        if not page_id(page):
            return {"error": "Find the page with notion-search, then pass its id or URL."}
        arguments = {"page_id": page_id(page), "command": "insert_content", "content": content}
        text, failed = await call(client, "notion-update-page", arguments)
        if failed:
            return {"error": text}
        title = page_title(runtime.state["messages"], page) or "the page"
        return {"added_to": {"title": title, "url": first_page_url(text) or page_url(page)}}

    @tool(parse_docstring=True)
    async def move_notion_pages(
        pages: list[str], runtime: ToolRuntime, new_parent: str | None = None
    ) -> dict[str, Any]:
        """Move Notion pages under another page; nothing on them changes. The user confirms
        first.

        Args:
            pages: The pages to move: their ids or URLs, from notion-search or notion-fetch,
                in the order they should end up in (e.g. alphabetical, if the user asks).
            new_parent: The page to put them under: its id or URL. Leave out to move them to
                the user's private pages.
        """
        if not within_move_limit(pages):
            return {"error": f"Move at most {MAX_MOVED} pages at once: split them into groups."}
        ids = [page_id(page) for page in pages]
        if not ids or None in ids or (new_parent and not page_id(new_parent)):
            return {"error": "Find each page with notion-search, then pass its id or URL."}
        destination = (
            {"type": "page_id", "page_id": page_id(new_parent)}
            if new_parent
            else {"type": "workspace"}  # the top level: the user's private pages
        )
        arguments = {"page_or_database_ids": ids, "new_parent": destination}
        text, failed = await call(client, "notion-move-pages", arguments)
        if failed:
            return {"error": text}
        messages = runtime.state["messages"]
        return {
            "moved": {
                "titles": [page_title(messages, page) for page in pages],  # None if never seen
                "where": under(messages, new_parent) if new_parent else "to your private pages",
                # Show where they now live: the new parent, or the page itself at the top level.
                "url": page_url(new_parent or pages[0]),
            }
        }

    return [create_notion_page, add_to_notion_page, move_notion_pages]


async def call(client: Client, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Call one of Notion's tools; returns its text and whether Notion reported an error."""
    async with client:  # reuses the request's open connection
        result = await client.call_tool(name, arguments, raise_on_error=False)
    text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
    return text, result.is_error


# --- Pages, ids and links ---
def page_id(text: str | None) -> str | None:
    """A page's id, from an id or a URL, as 32 hex digits.

    Dashes carry no meaning: Notion writes ids both ways, and a model copying a long id can
    misplace one ("c3d4e5f60000-4000-…", seen live). Only the digits count.
    """
    for run in HEX_RUN.findall((text or "").lower()):
        digits = run.replace("-", "")
        if len(digits) >= 32:
            return digits[-32:]  # in a URL ("…/Trek-notes-a1b2c3d4…") the id comes last
    return None


def page_url(page: str) -> str:
    return page if page.startswith("http") else f"https://www.notion.so/{page_id(page)}"


def first_page_url(text: str) -> str | None:
    found = re.search(r"https://[\w.]*notion\.(?:so|com)/[^\s\"'<>)\]]+", text)
    return found.group() if found else None


def page_title(messages: Sequence[AnyMessage], page: str | None) -> str | None:
    """The page's title as a search, fetch or database listing showed it earlier in this request."""
    wanted = page_id(page)
    if wanted is None:
        return None
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name in TITLE_SOURCES:
            for shown in pages_in(message.text):
                if wanted in {page_id(shown.get("id")), page_id(shown.get("url"))}:
                    return (shown.get("title") or "").strip() or None
    return None


def pages_in(text: str) -> list[dict[str, Any]]:
    """The pages a result names: a search's results, a listing's pages, or a fetched page."""
    result = json_object(text) or {}
    pages = result.get("results") or result.get("pages") or [result]
    return [page for page in pages if isinstance(page, dict)]


def json_object(text: str) -> dict[str, Any] | None:
    """`text` as a JSON object, or None if it isn't one."""
    try:
        found = json.loads(text)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def under(messages: Sequence[AnyMessage], parent: str) -> str:
    """Where a page goes, in words: "under “Health”", by the title the agent saw."""
    return f"under {quoted(page_title(messages, parent), UNSEEN_PAGE)}"


def quoted(title: str | None, unknown: str) -> str:
    return f"“{title}”" if title else unknown


def listed(names: Sequence[str]) -> str:
    """ "“Trek notes”, “Packing” and “Health”": every name, so nothing is approved unheard.

    (An early version said "and 6 more": the question-writer, told never to drop a fact, asked
    the user for the missing names instead of asking yes or no.)
    """
    if len(names) < 2:
        return "".join(names) or "no pages"
    return f"{', '.join(names[:-1])} and {names[-1]}"


# --- Exact words for approvals (built by code from the arguments, so they can't drift) ---
def describe_new_page(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    args = tool_call["args"]
    parent = args.get("parent")
    where = under(state["messages"], parent) if parent else "as a new private page"
    title = args.get("title") or "(untitled)"
    return f"Create the Notion page “{title}” {where}, saying: {preview(args.get('content', ''))}"


def describe_page_addition(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    args = tool_call["args"]
    title = page_title(state["messages"], args.get("page"))
    page = f"the Notion page “{title}”" if title else UNSEEN_PAGE
    return f"Add to the end of {page}: {preview(args.get('content', ''))}"


def within_move_limit(pages: Sequence[str]) -> bool:
    return len(pages) <= MAX_MOVED


def ask_about_move(request: ToolCallRequest) -> bool:
    """Ask only about moves that can happen: one over the limit is refused, so never asked about.
    (Safe as a `when` check: the number of pages can't change between asking and running.)"""
    return within_move_limit(request.tool_call["args"].get("pages", []))


def describe_move(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    args, messages = tool_call["args"], state["messages"]
    pages = [quoted(page_title(messages, page), UNSEEN_PAGE) for page in args.get("pages", [])]
    parent = args.get("new_parent")
    where = under(messages, parent) if parent else "to your private pages"
    return f"Move the Notion {'page' if len(pages) == 1 else 'pages'} {listed(pages)} {where}."


# --- Showing you the page ---
class ShowNotionPages(AgentMiddleware):
    """Opens each Notion page the agent reads or writes in the Notion app, so you see where.

    Middleware, because notion-fetch is Notion's own tool: this adds behaviour around a tool we
    don't own. A page opens once per request, and only after its call succeeds.
    """

    def __init__(self) -> None:
        super().__init__()
        self.shown: set[str] = set()

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if isinstance(result, ToolMessage) and result.status != "error":
            url = page_shown_by(request.tool_call["name"], result.text)
            if url and page_id(url) not in self.shown:
                self.shown.add(page_id(url))
                await open_in_notion(url)
        return result


SHOWN_BY = {"notion-fetch", "create_notion_page", "add_to_notion_page", "move_notion_pages"}


def page_shown_by(tool_name: str, result_text: str) -> str | None:
    """The page a successful notion-fetch, or one of our writes, was about."""
    result = json_object(result_text) if tool_name in SHOWN_BY else None
    if result is None:
        return None
    if tool_name == "notion-fetch":
        return result.get("url")
    written = result.get("created") or result.get("added_to") or result.get("moved") or {}
    return written.get("url")


async def open_in_notion(url: str) -> None:
    """Show the page in the Notion app: a notion:// link opens the app instead of the browser."""
    link = re.sub(r"^https?://", "notion://", url)
    opener = await asyncio.create_subprocess_exec(
        "open", link, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await opener.wait()  # `open` hands over to macOS and returns at once
