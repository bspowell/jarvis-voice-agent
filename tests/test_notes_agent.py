"""The Notes agent end to end: Brain → Jarvis → Notes agent → your OK → Notion or your files.

Every model is scripted; Notion is a real MCP server running in memory (`FakeNotion`); the
Keychain is a fake. The real tools, MCP adapter, middleware, interrupts and Brain run between.
"""

import json

import pytest
from fakes import (
    DATABASE,
    HEALTH,
    NEW_PAGE_URL,
    TREK_NOTES,
    VENICE_TRIP,
    FakeApprovalModel,
    FakeNotion,
    FakeToolCallingModel,
    notion_page,
)
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.agents import notes
from jarvis.agents.notes import FILE_TOOLS, WRITE_TOOLS, check_notes, make_notes_tool
from jarvis.auth.notion import notion_store
from jarvis.auth.store import NotSignedIn
from jarvis.brain.approvals import Approvals, Reading
from jarvis.brain.brain import Brain
from jarvis.brain.memory import RepairInterruptedToolCalls
from jarvis.config import Settings
from jarvis.events import Event
from jarvis.tools import notion
from jarvis.tools.notion import (
    CONNECT_FIRST,
    MAX_MOVED,
    describe_move,
    describe_new_page,
    notion_tools,
    page_id,
)

QUESTION = "Shall I add a private page, Dentist on Friday, sir?"
MOVE_TREK_NOTES = "Move the Notion page “Trek notes” under “Health”."


def calls(tool: str, call_id: str, **args) -> AIMessage:
    """A scripted model message that calls one tool."""
    return AIMessage("", tool_calls=[ToolCall(name=tool, args=args, id=call_id)])


def asks_notes(request: str) -> AIMessage:
    return calls("notes_agent", "j1", request=request)


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "Desktop").mkdir()
    return Settings(_env_file=None, files={"folders": [str(tmp_path / "Desktop")]})


@pytest.fixture
def fake_notion(monkeypatch):
    fake = FakeNotion()
    monkeypatch.setattr(notion, "notion_client", lambda settings: fake.client())
    return fake


@pytest.fixture
def opened(monkeypatch):
    """Pages the agent opened in the Notion app (never really opened in tests)."""
    pages: list[str] = []

    async def open_in_notion(url: str) -> None:
        pages.append(url)

    monkeypatch.setattr(notion, "open_in_notion", open_in_notion)
    return pages


def connect_notion(settings: Settings) -> None:
    notion_store(settings).save({"tokens": {"access_token": "notion-token"}})


def brain_with(monkeypatch, settings, *, jarvis_says, notes_say, readings=(), questions=()):
    """The real notes_agent tool, Jarvis's loop and Brain, with scripted models."""
    notes_model = FakeToolCallingModel(messages=iter(notes_say))
    monkeypatch.setattr(notes, "make_model", lambda name: notes_model)
    jarvis = create_agent(
        FakeToolCallingModel(messages=iter(jarvis_says)),
        tools=[make_notes_tool(settings)],
        middleware=[RepairInterruptedToolCalls()],
        checkpointer=InMemorySaver(),
        name="jarvis",
    )
    approvals_model = FakeApprovalModel(messages=iter(questions), readings=list(readings))
    return Brain(jarvis, approvals=Approvals(approvals_model)), notes_model, approvals_model


async def say(brain: Brain, text: str) -> str:
    return "".join([chunk async for chunk in brain.turn(text)])


async def report_to_jarvis(brain: Brain) -> str:
    """What the notes_agent tool handed back to Jarvis (its last result)."""
    messages = (await brain.agent.aget_state(brain.config())).values["messages"]
    return next(m for m in reversed(messages) if isinstance(m, ToolMessage)).text


# --- Reading ---
async def test_finding_a_note_reads_it_and_shows_it_in_notion(
    monkeypatch, settings, fake_notion, opened
):
    connect_notion(settings)
    report = "Your trek notes, in your Personal pages, say Venice is best in May."
    brain, _, _ = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Find my trek notes"), AIMessage("May, sir. Venice.")],
        notes_say=[
            calls("notion-search", "n1", query="trek"),
            calls("notion-fetch", "n2", id=TREK_NOTES["id"]),
            AIMessage(report),
        ],
    )
    events = []
    brain.events.subscribe(events.append)

    assert await say(brain, "Find my trek notes") == "May, sir. Venice."
    assert opened == [TREK_NOTES["url"]]  # the page you'd want to see, opened once
    assert events == [Event("agent_started", {"agent": "notes"})]
    assert await report_to_jarvis(brain) == report  # only the report: pages stay with the agent


# --- Writing to Notion: only after a yes ---
async def test_a_new_note_waits_for_yes_then_becomes_a_private_page(
    monkeypatch, settings, fake_notion, opened
):
    connect_notion(settings)
    brain, _, approvals_model = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Note: dentist on Friday at 3"), AIMessage("Noted, sir.")],
        notes_say=[
            calls("create_notion_page", "c1", title="Dentist on Friday", content="At 3 PM."),
            AIMessage("Created a private page, Dentist on Friday."),
        ],
        readings=[Reading(decision="approve")],
        questions=[QUESTION],
    )
    assert await say(brain, "Note: dentist on Friday at 3") == QUESTION
    assert fake_notion.created == []  # paused just before the write
    asked = approvals_model.seen[0][-1].text
    assert "Create the Notion page “Dentist on Friday” as a new private page" in asked

    assert await say(brain, "Yes") == "Noted, sir."
    [created] = fake_notion.created
    assert created["creation_mode"] == "draft"  # Notion's "private page" mode
    assert created["pages"] == [
        {"properties": {"title": "Dentist on Friday"}, "content": "At 3 PM."}
    ]
    assert opened == [NEW_PAGE_URL]
    assert (await report_to_jarvis(brain)).endswith(
        "Saved: created the Notion page “Dentist on Friday” in your private pages."
    )


async def test_no_means_nothing_is_saved_and_jarvis_is_told(
    monkeypatch, settings, fake_notion, opened
):
    connect_notion(settings)
    brain, _, _ = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Note: dentist on Friday"), AIMessage("Very good, sir.")],
        notes_say=[
            calls("create_notion_page", "c1", title="Dentist on Friday", content="At 3 PM."),
            # Found live: after a no, Haiku offered alternatives, and Jarvis called it a snag.
            AIMessage("The note wasn't saved. Shall I save it as a file instead?"),
        ],
        readings=[Reading(decision="reject")],
        questions=[QUESTION],
    )
    await say(brain, "Note: dentist on Friday")
    await say(brain, "No, leave it")
    assert fake_notion.created == []
    assert opened == []
    assert (await report_to_jarvis(brain)).endswith(
        "While approving, the user said no, so nothing was saved."
    )


async def test_put_it_under_health_is_proposed_again_and_needs_its_own_yes(
    monkeypatch, settings, fake_notion, opened
):
    connect_notion(settings)
    brain, notes_model, approvals_model = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Note: dentist on Friday"), AIMessage("Filed under Health, sir.")],
        notes_say=[
            calls("create_notion_page", "c1", title="Dentist on Friday", content="At 3 PM."),
            calls("notion-search", "s1", query="Health"),
            calls(
                "create_notion_page",
                "c2",
                title="Dentist on Friday",
                content="At 3 PM.",
                parent=HEALTH["url"],
            ),
            AIMessage("Created Dentist on Friday under Health."),
        ],
        readings=[
            Reading(decision="change", change="put it under the Health page instead"),
            Reading(decision="approve"),
        ],
        questions=[QUESTION, "Shall I put it under Health instead?"],
    )
    await say(brain, "Note: dentist on Friday")
    assert await say(brain, "Put it under Health") == "Shall I put it under Health instead?"
    assert fake_notion.created == []
    assert "under the Health page" in notes_model.seen[1][-1].text  # the agent heard the change
    assert "under “Health”" in approvals_model.seen[1][-1].text  # the title, found by code

    await say(brain, "Perfect")
    [created] = fake_notion.created
    assert created["parent"] == {"page_id": HEALTH["id"].replace("-", "")}
    assert (await report_to_jarvis(brain)).endswith(
        "While approving, the user asked for: put it under the Health page instead.\n"
        "Saved: created the Notion page “Dentist on Friday” under “Health”."
    )


async def test_adding_to_a_page_only_ever_appends(monkeypatch, settings, fake_notion, opened):
    connect_notion(settings)
    brain, _, _ = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Add map to my trek notes"), AIMessage("Added, sir.")],
        notes_say=[
            calls("notion-search", "s1", query="trek"),
            calls("add_to_notion_page", "a1", page=TREK_NOTES["id"], content="- Buy map"),
            AIMessage("Added it."),
        ],
        readings=[Reading(decision="approve")],
        questions=["Shall I add buy map to your trek notes?"],
    )
    await say(brain, "Add map to my trek notes")
    assert fake_notion.updated == []
    await say(brain, "Go ahead")
    assert fake_notion.updated == [
        {
            "page_id": TREK_NOTES["id"].replace("-", ""),
            "command": "insert_content",
            "content": "- Buy map",
        }
    ]
    assert (await report_to_jarvis(brain)).endswith("Saved: added to the Notion page “Trek notes”.")


async def test_moving_pages_waits_for_yes_and_shows_where_they_went(
    monkeypatch, settings, fake_notion, opened
):
    connect_notion(settings)
    brain, _, approvals_model = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Move my trek notes under Health"), AIMessage("Moved, sir.")],
        notes_say=[
            calls("notion-search", "s1", query="trek health"),
            calls("move_notion_pages", "m1", pages=[TREK_NOTES["url"]], new_parent=HEALTH["id"]),
            AIMessage("Moved Trek notes under Health."),
        ],
        readings=[Reading(decision="approve")],
        questions=["Shall I move your trek notes under Health?"],
    )
    events = []
    brain.events.subscribe(events.append)
    await say(brain, "Move my trek notes under Health")
    assert fake_notion.moved == []  # paused just before the move
    # Under the spoken question, the chat shows exactly what a yes would do.
    assert Event("approval_needed", {"changes": [MOVE_TREK_NOTES]}) in events
    assert MOVE_TREK_NOTES in approvals_model.seen[0][-1].text

    await say(brain, "Yes")
    assert fake_notion.moved == [
        {
            "ids": [TREK_NOTES["id"].replace("-", "")],
            "new_parent": {"type": "page_id", "page_id": HEALTH["id"].replace("-", "")},
        }
    ]
    assert opened == [f"https://www.notion.so/{HEALTH['id'].replace('-', '')}"]  # its new home
    assert (await report_to_jarvis(brain)).endswith("Saved: moved “Trek notes” under “Health”.")


def test_a_long_move_still_names_every_page():
    """Found live: "…and 6 more" made the question-writer ask for names instead of yes or no."""
    titles = [f"Page {letter}" for letter in "ABCDEFGHIJK"]  # eleven pages
    ids = [f"{index:032x}" for index in range(1, 12)]
    search = ToolMessage(
        json.dumps({"results": [{"id": i, "title": t} for i, t in zip(ids, titles, strict=True)]}),
        name="notion-search",
        tool_call_id="s1",
    )
    move = calls("move_notion_pages", "m1", pages=ids, new_parent=ids[0]).tool_calls[0]
    described = describe_move(move, {"messages": [search]}, None)
    assert all(f"“{title}”" in described for title in titles)
    assert "more" not in described


def test_an_id_with_a_misplaced_dash_still_counts():
    """Found live: Haiku copied "c3d4e5f6-0000-…" as "c3d4e5f60000-…"; the digits were right."""
    assert page_id("c3d4e5f60000-4000-8000-000000000004") == "c3d4e5f6000040008000000000000004"
    assert page_id("https://www.notion.so/Trek-notes-a1b2c3d4000040008000000000000001") == (
        "a1b2c3d4000040008000000000000001"
    )
    assert page_id("Health") is None  # a title, not an id


def test_a_move_names_every_page_and_where_they_go():
    search = ToolMessage(
        '{"results": [{"id": "a1b2c3d4-0000-4000-8000-000000000001", "title": "Trek notes"},'
        ' {"id": "1a2b3c4d-1111-4222-8333-444455556666", "title": "Health"}]}',
        name="notion-search",
        tool_call_id="s1",
    )
    move = calls(
        "move_notion_pages",
        "m1",
        pages=[TREK_NOTES["id"], HEALTH["id"], "ffffffffffffffffffffffffffffffff"],
    ).tool_calls[0]
    assert describe_move(move, {"messages": [search]}, None) == (
        "Move the Notion pages “Trek notes”, “Health” and a page it didn't look up first "
        "to your private pages."
    )


async def test_a_database_lists_its_own_pages_not_the_ones_inside_them(settings, fake_notion):
    """Found live: Notion's search inside a database also returns every nested page (47, not 11)."""
    connect_notion(settings)
    async with notion_tools(settings) as tools:
        lister = next(tool for tool in tools if tool.name == "list_database_pages")
        listed = await lister.ainvoke({"database": DATABASE["url"]})
    assert [page["title"] for page in listed["pages"]] == ["Trek notes", "Health"]
    assert VENICE_TRIP["title"] not in str(listed)  # inside Trek notes: not a database row


async def test_a_long_database_is_read_page_by_page(settings, monkeypatch):
    rows = [notion_page(f"{n:032x}", f"Row {n}", "Personal", "") for n in range(1, 151)]
    fake = FakeNotion(rows=rows)
    monkeypatch.setattr(notion, "notion_client", lambda settings: fake.client())
    connect_notion(settings)
    async with notion_tools(settings) as tools:
        lister = next(tool for tool in tools if tool.name == "list_database_pages")
        listed = await lister.ainvoke({"database": DATABASE["url"]})
    assert len(listed["pages"]) == 150  # Notion sends 100 at a time


async def test_a_move_over_the_limit_is_never_asked_and_never_runs(
    monkeypatch, settings, fake_notion, opened
):
    connect_notion(settings)
    too_many = [f"{n:032x}" for n in range(1, MAX_MOVED + 2)]
    brain, _, approvals_model = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Move all of them under Health"), AIMessage("Too many, sir.")],
        notes_say=[
            calls("move_notion_pages", "m1", pages=too_many, new_parent=HEALTH["id"]),
            AIMessage("That's more than 25 pages: nothing moved."),
        ],
    )
    assert await say(brain, "Move all of them under Health") == "Too many, sir."
    assert approvals_model.seen == []  # nobody was asked about a move that can't happen
    assert fake_notion.moved == []


# --- Files ---
async def test_a_file_note_waits_for_yes_too(monkeypatch, settings, tmp_path, fake_notion, opened):
    monkeypatch.setenv("HOME", str(tmp_path))  # so "~/Desktop" is the test's folder
    groceries = tmp_path / "Desktop" / "groceries.md"
    groceries.write_text("- bread\n")
    brain, _, _ = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Add milk to my groceries"), AIMessage("Milk, noted.")],
        notes_say=[
            calls("find_files", "f1", words="groceries"),
            calls("add_to_file", "f2", path="~/Desktop/groceries.md", text="- milk"),
            AIMessage("Added milk."),
        ],
        readings=[Reading(decision="approve")],
        questions=["Shall I add milk to groceries.md on your Desktop?"],
    )
    await say(brain, "Add milk to my groceries")
    assert groceries.read_text() == "- bread\n"  # paused just before the write
    await say(brain, "Yes")
    assert groceries.read_text() == "- bread\n- milk\n"
    assert (await report_to_jarvis(brain)).endswith(
        "Saved: added to the file ~/Desktop/groceries.md."
    )
    assert opened == []  # files don't open Notion


# --- Not connected ---
async def test_without_notion_a_notion_request_shows_the_connect_button(
    monkeypatch, settings, fake_notion
):
    brain, _, _ = brain_with(
        monkeypatch,
        settings,
        jarvis_says=[asks_notes("Find my trek notes"), AIMessage("Notion first, sir.")],
        notes_say=[calls("connect_notion", "x1"), AIMessage("Notion isn't connected.")],
    )
    events = []
    brain.events.subscribe(events.append)

    await say(brain, "Find my trek notes")
    assert events == [
        Event("agent_started", {"agent": "notes"}),
        Event("sign_in_needed", {"service": "notion"}),
    ]


async def test_a_sign_in_that_stopped_working_is_forgotten(monkeypatch, settings):
    connect_notion(settings)

    class RejectedAdapter:
        """Connecting fails the way FastMCP reports a sign-in Notion no longer accepts."""

        def __init__(self, client) -> None: ...

        async def __aenter__(self):
            raise RuntimeError("Client failed to connect") from NotSignedIn("Not connected")

        async def __aexit__(self, *exc_info) -> None: ...

    monkeypatch.setattr(notion, "MCPAdapter", RejectedAdapter)
    async with notion_tools(settings) as tools:
        assert tools is None  # the agent gets connect_notion instead
    assert notion_store(settings).saved() is None  # so the button shows from now on


async def test_connect_notion_says_a_button_is_waiting():
    assert await notion.connect_notion.ainvoke({}) == CONNECT_FIRST


# --- Safety, by construction ---
async def test_only_six_notion_tools_are_offered(settings, fake_notion):
    connect_notion(settings)
    async with notion_tools(settings) as tools:
        offered = {tool.name for tool in tools}
    assert offered == {
        "notion-search",
        "notion-fetch",
        "list_database_pages",
        "create_notion_page",
        "add_to_notion_page",
        "move_notion_pages",
    }  # Notion's own move, query and duplicate tools stay out of reach


async def test_every_tool_that_writes_needs_approval(settings, fake_notion):
    connect_notion(settings)
    async with notion_tools(settings) as tools:
        every_tool = {tool.name for tool in [*FILE_TOOLS, *tools]}
    reads = {"find_files", "list_folder", "read_file", "notion-search", "notion-fetch"}
    reads.add("list_database_pages")
    assert every_tool - reads == set(WRITE_TOOLS)
    assert not any(word in name for name in every_tool for word in ("delete", "remove", "replace"))


def test_a_parent_it_never_looked_up_is_named_as_such():
    proposal = calls(
        "create_notion_page", "c1", title="Ideas", content="x", parent=HEALTH["id"]
    ).tool_calls[0]
    assert "under a page it didn't look up first" in describe_new_page(
        proposal, {"messages": []}, None
    )


# --- Health check ---
async def test_the_health_check_counts_notion_when_connected(settings, fake_notion):
    assert "Notion not connected yet" in await check_notes(settings)
    connect_notion(settings)
    assert await check_notes(settings) == "Ready: Notion and files in 1 folders."
