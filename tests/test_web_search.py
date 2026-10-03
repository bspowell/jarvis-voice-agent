from fakes import BLOG, LANGGRAPH_PAGES, LANGGRAPH_RESULTS, PYPI, RELEASES, FakeFirecrawl
from firecrawl.v2.types import SearchResultWeb
from firecrawl.v2.utils.error_handler import WebsiteNotSupportedError
from langchain_core.utils.function_calling import convert_to_openai_tool

from jarvis.tools.web_search import (
    FULL_PAGE_CHARS,
    MAX_PAGES_TO_READ,
    PREVIEW_CHARS,
    SNIPPET_CHARS,
    clean,
    read_page,
    search_and_read,
    web_search,
)


def test_model_sees_query_and_how_many_pages_to_read():
    schema = convert_to_openai_tool(web_search)["function"]["parameters"]
    assert list(schema["properties"]) == ["query", "pages_to_read"]
    assert schema["required"] == ["query"]  # quick by default: reads the top 2 pages


async def test_search_reads_only_the_top_pages():
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    found = await search_and_read(firecrawl, "LangGraph latest release", pages_to_read=2)

    assert [result["url"] for result in found["results"]] == [RELEASES, PYPI, BLOG]
    assert found["results"][1]["snippet"] == "Released: Sep 30, 2026"
    assert [page["url"] for page in found["pages"]] == [RELEASES, PYPI]
    assert found["pages"][1]["text"] == "# langgraph 1.2.12\n\nReleased: Sep 30, 2026"
    assert firecrawl.reads == [RELEASES, PYPI]  # the blog was never fetched: one credit saved


async def test_pages_to_read_stays_within_budget():
    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS * 2, LANGGRAPH_PAGES)
    await search_and_read(firecrawl, "LangGraph", pages_to_read=50)
    assert len(firecrawl.reads) == MAX_PAGES_TO_READ

    firecrawl = FakeFirecrawl(LANGGRAPH_RESULTS, LANGGRAPH_PAGES)
    found = await search_and_read(firecrawl, "LangGraph", pages_to_read=0)
    assert (found["pages"], firecrawl.reads) == ([], [])  # snippets only


async def test_unreadable_page_is_a_fact_not_a_crash():
    pages = {**LANGGRAPH_PAGES, RELEASES: WebsiteNotSupportedError("Website Not Supported")}
    found = await search_and_read(FakeFirecrawl(LANGGRAPH_RESULTS, pages), "LangGraph", 2)
    blocked, readable = found["pages"]
    assert blocked == {
        "url": RELEASES,
        "error": "Couldn't read this page (WebsiteNotSupportedError).",
    }
    assert readable["text"].endswith("Released: Sep 30, 2026")  # the other page still arrives


LONG_PAGE = "word " * 5_000  # 25,000 characters


async def test_previews_are_short_and_say_when_they_were_cut():
    firecrawl = FakeFirecrawl([LANGGRAPH_RESULTS[2]], {BLOG: LONG_PAGE})
    (preview,) = (await search_and_read(firecrawl, "LangGraph", pages_to_read=1))["pages"]
    assert (len(preview["text"]), preview["cut_short"]) == (PREVIEW_CHARS, True)


async def test_reading_one_page_on_purpose_gets_much_more(monkeypatch):
    firecrawl = FakeFirecrawl(pages={BLOG: LONG_PAGE})
    monkeypatch.setattr("jarvis.tools.web_search.firecrawl_client", lambda: firecrawl)
    page = await read_page.ainvoke({"url": BLOG})
    assert (len(page["text"]), page["cut_short"]) == (FULL_PAGE_CHARS, True)


def test_clean_keeps_the_words_and_drops_addresses():
    markdown = "![logo](/logo.png)\nRead [the release notes](https://x.dev/notes).\n\n\n\nThe end."
    assert clean(markdown) == "Read the release notes.\n\nThe end."


async def test_snippets_are_cleaned_and_trimmed():
    noisy = SearchResultWeb(url=BLOG, title="Blog", description="[1.2.12](https://x.dev) " * 500)
    found = await search_and_read(FakeFirecrawl([noisy]), "LangGraph", pages_to_read=0)
    snippet = found["results"][0]["snippet"]
    assert snippet.startswith("1.2.12 1.2.12")
    assert len(snippet) == SNIPPET_CHARS
