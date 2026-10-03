"""Web search tools for the Search agent, powered by Firecrawl.

- `web_search`: the top results for a query, plus a preview of the top pages.
- `read_page`: one page in full, e.g. when a preview was cut short.

Firecrawl charges credits: 2 per search (up to 10 results) and 1 per page read.
"""

import asyncio
import re
from functools import cache
from typing import Any

import httpx
from firecrawl import AsyncFirecrawl
from firecrawl.v2.utils.error_handler import FirecrawlError
from langchain.tools import tool

from jarvis.config import get_settings

RESULTS = 5  # results per search: Firecrawl charges the same for 1 to 10
PAGES_TO_READ = 2  # quick by default: the top two pages usually hold the answer
MAX_PAGES_TO_READ = 4
PREVIEW_CHARS = 6_000  # each top page ≈1,500 tokens: keeps quick searches quick and cheap
FULL_PAGE_CHARS = 20_000  # read_page, when the agent chooses to read one page properly
SNIPPET_CHARS = 1_500  # a result's snippet: the passages that match the query
SERVICE_TIMEOUT_MS = 15_000  # Firecrawl gives up after this…
CLIENT_TIMEOUT_SECONDS = 20  # …and so do we, a little later, if it never answers


@tool(parse_docstring=True)
async def web_search(query: str, pages_to_read: int = PAGES_TO_READ) -> dict[str, Any]:
    """Search the web: the top results, plus a preview of the top pages' text.

    Args:
        query: Search words, e.g. "LangGraph latest release". Add the year for recent events.
        pages_to_read: How many of the top results to preview (0 to 4).
    """
    return await search_and_read(firecrawl_client(), query, pages_to_read)


@tool(parse_docstring=True)
async def read_page(url: str) -> dict[str, Any]:
    """Read one web page in full, e.g. when its preview was cut short or it wasn't previewed.

    Args:
        url: The page's full address, from a search result.
    """
    return await read(firecrawl_client(), url, FULL_PAGE_CHARS)


@cache
def firecrawl_client() -> AsyncFirecrawl:
    """One client for the whole run (it keeps no connections open between requests)."""
    api_key = get_settings().firecrawl_api_key
    if api_key is None:  # Jarvis's search_agent tool checks first, so this is a wiring bug
        raise RuntimeError("FIRECRAWL_API_KEY is missing from .env.")
    return AsyncFirecrawl(
        api_key=api_key.get_secret_value(), timeout=CLIENT_TIMEOUT_SECONDS, max_retries=2
    )


async def firecrawl_status(client: AsyncFirecrawl) -> str:
    """How Firecrawl is doing, for health checks. Costs no credits. Raises if it can't search."""
    usage = await client.get_credit_usage()  # also proves the key works
    if usage.remaining_credits <= 0:
        raise RuntimeError("Firecrawl is out of credits.")
    return f"Firecrawl ready, {usage.remaining_credits} credits left."


async def search_and_read(client: AsyncFirecrawl, query: str, pages_to_read: int) -> dict[str, Any]:
    """Compact facts: every result's snippet, and the text of the top pages (read in parallel)."""
    found = await client.search(query, limit=RESULTS, timeout=SERVICE_TIMEOUT_MS)
    results = found.web or []
    top = results[: max(0, min(pages_to_read, MAX_PAGES_TO_READ))]
    return {
        "results": [
            {
                "title": result.title,
                "url": result.url,
                "snippet": clean(result.description or "")[:SNIPPET_CHARS],
            }
            for result in results
        ],
        "pages": await asyncio.gather(*(read(client, r.url, PREVIEW_CHARS) for r in top)),
    }


async def read(client: AsyncFirecrawl, url: str, max_chars: int) -> dict[str, Any]:
    """A page's main text, cleaned then trimmed. A page that can't be read is a fact."""
    try:
        page = await client.scrape(
            url, formats=["markdown"], only_main_content=True, timeout=SERVICE_TIMEOUT_MS
        )
    except (FirecrawlError, httpx.HTTPError) as error:  # blocked, timed out, gone…
        return {"url": url, "error": f"Couldn't read this page ({type(error).__name__})."}
    text = clean(page.markdown or "")
    return {
        "url": url,
        "title": page.metadata_typed.title,
        "text": text[:max_chars],
        "cut_short": len(text) > max_chars,  # tells the agent there's more to read
    }


IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")  # ![alt](picture.png)
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # [words](https://…) → words
EXTRA_BLANK_LINES = re.compile(r"\n{3,}")


def clean(markdown: str) -> str:
    """Keep the words; drop images, link addresses and extra blank lines (wasted tokens)."""
    text = IMAGE.sub("", markdown)
    text = LINK.sub(r"\1", text)
    return EXTRA_BLANK_LINES.sub("\n\n", text).strip()
