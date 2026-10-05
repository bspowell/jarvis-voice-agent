"""The Calendar agent end to end: Brain → Jarvis → Calendar agent → your OK → Google.

Every model is scripted, Google and the Keychain are fakes, and the real tool, middleware,
interrupts and Brain run in between.
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

from fakes import FakeApprovalModel, FakeGoogleCalendar, FakeToolCallingModel
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.agents import calendar
from jarvis.agents.calendar import READ_TOOLS, SIGN_IN_FIRST, TOOLS, WRITE_TOOLS, make_calendar_tool
from jarvis.auth.google import google_store
from jarvis.brain.approvals import Approvals, Reading
from jarvis.brain.brain import Brain
from jarvis.brain.memory import INTERRUPTED, RepairInterruptedToolCalls
from jarvis.config import Settings
from jarvis.events import Event
from jarvis.tools import google_calendar

SETTINGS = Settings(
    _env_file=None,
    google_client_id="jarvis.apps.googleusercontent.com",
    google_client_secret="app-secret",
)
BOOK_LUNCH = "Book lunch with Sam tomorrow at noon"
QUESTION = "Shall I book lunch with Sam for Monday at noon, sir?"
ASKS_CALENDAR = AIMessage(
    "", tool_calls=[ToolCall(name="calendar_agent", args={"request": BOOK_LUNCH}, id="j1")]
)


def proposes_lunch(start: str, call_id: str) -> AIMessage:
    """The Calendar agent's model asking to create the event (which pauses for approval)."""
    args = {"title": "Lunch with Sam", "start": start}
    return AIMessage("", tool_calls=[ToolCall(name="create_event", args=args, id=call_id)])


def signed_in() -> None:
    google_store(SETTINGS).save(
        {"token": "access-1", "refresh_token": "refresh-1", "expiry": "2099-01-01T00:00:00Z"}
    )


def brain_with(monkeypatch, *, jarvis_says, calendar_says, readings=(), questions=(QUESTION,)):
    """The real calendar_agent tool, Jarvis's loop and Brain, with scripted models and fakes."""
    google = FakeGoogleCalendar()

    @asynccontextmanager
    async def google_client():
        yield google

    monkeypatch.setattr(google_calendar, "calendar_client", google_client)
    calendar_model = FakeToolCallingModel(messages=iter(calendar_says))
    monkeypatch.setattr(calendar, "make_model", lambda name: calendar_model)
    jarvis = create_agent(
        FakeToolCallingModel(messages=iter(jarvis_says)),
        tools=[make_calendar_tool(SETTINGS)],
        middleware=[RepairInterruptedToolCalls()],
        checkpointer=InMemorySaver(),
        name="jarvis",
    )
    approvals_model = FakeApprovalModel(messages=iter(questions), readings=list(readings))
    brain = Brain(jarvis, approvals=Approvals(approvals_model))
    return brain, google, calendar_model, approvals_model


async def say(brain: Brain, text: str) -> str:
    return "".join([chunk async for chunk in brain.turn(text)])


async def report_to_jarvis(brain: Brain) -> str:
    """What the calendar_agent tool handed back to Jarvis (its last result)."""
    messages = (await brain.agent.aget_state(brain.config())).values["messages"]
    return next(m for m in reversed(messages) if isinstance(m, ToolMessage)).text


async def test_nothing_is_saved_until_you_say_yes(monkeypatch):
    signed_in()
    brain, google, calendar_model, approvals_model = brain_with(
        monkeypatch,
        jarvis_says=[ASKS_CALENDAR, AIMessage("Done, sir.")],
        calendar_says=[proposes_lunch("2026-10-05T12:00", "c1"), AIMessage("Booked.")],
        readings=[Reading(decision="approve")],
    )
    assert await say(brain, BOOK_LUNCH) == QUESTION
    assert google.created == []  # paused just before the write

    assert await say(brain, "Yes please") == "Done, sir."
    assert len(google.created) == 1  # written once, after the yes
    report = await report_to_jarvis(brain)
    assert (
        report
        == "Booked.\nSaved in the calendar: added “Lunch with Sam”, Mon 5 Oct 2026, 12 PM to 1 PM."
    )
    assert approvals_model.read_requests[0][-1].text.endswith("The user replied: Yes please")
    # The Calendar agent carried on from where it paused: same task, nothing started over.
    resumed = calendar_model.seen[1]
    assert sum(isinstance(message, HumanMessage) for message in resumed) == 1


async def test_no_means_nothing_is_saved(monkeypatch):
    signed_in()
    brain, google, calendar_model, _ = brain_with(
        monkeypatch,
        jarvis_says=[ASKS_CALENDAR, AIMessage("Very good, sir.")],
        calendar_says=[proposes_lunch("2026-10-05T12:00", "c1"), AIMessage("Nothing booked.")],
        readings=[Reading(decision="reject")],
    )
    await say(brain, BOOK_LUNCH)
    assert await say(brain, "No, leave it") == "Very good, sir."
    assert google.created == []
    rejection = calendar_model.seen[1][-1]  # what the Calendar agent heard
    assert isinstance(rejection, ToolMessage)
    assert "said no" in rejection.text
    # Jarvis never heard the "no": the receipt tells him, whatever the report's wording.
    assert (await report_to_jarvis(brain)).endswith(
        "While approving, the user said no, so nothing was saved."
    )


async def test_a_change_is_proposed_again_and_needs_its_own_yes(monkeypatch):
    signed_in()
    brain, google, calendar_model, _ = brain_with(
        monkeypatch,
        jarvis_says=[ASKS_CALENDAR, AIMessage("Done, sir.")],
        calendar_says=[
            proposes_lunch("2026-10-05T12:00", "c1"),
            proposes_lunch("2026-10-05T13:00", "c2"),
            AIMessage("Booked for 1 PM."),
        ],
        readings=[
            Reading(decision="change", change="make it 1 PM"),
            Reading(decision="approve"),
        ],
        questions=[QUESTION, "Shall I make it 1 PM instead?"],
    )
    await say(brain, BOOK_LUNCH)
    assert await say(brain, "Make it 1 PM") == "Shall I make it 1 PM instead?"
    assert google.created == []  # the changed version is asked about before it's saved
    assert "make it 1 PM" in calendar_model.seen[1][-1].text  # the agent heard what to change

    assert await say(brain, "Perfect") == "Done, sir."
    [(_calendar, event)] = google.created
    assert event["start"]["dateTime"] == "2026-10-05T13:00:00"
    # Jarvis never heard "make it 1 PM": the receipt tells him it was your choice, and what saved.
    assert (await report_to_jarvis(brain)).endswith(
        "While approving, the user asked for: make it 1 PM.\n"
        "Saved in the calendar: added “Lunch with Sam”, Mon 5 Oct 2026, 1 PM to 2 PM."
    )


async def test_changing_the_subject_drops_the_change(monkeypatch):
    signed_in()
    brain, google, _, _ = brain_with(
        monkeypatch,
        jarvis_says=[ASKS_CALENDAR, AIMessage("Sunny, sir. Lunch remains unbooked.")],
        calendar_says=[proposes_lunch("2026-10-05T12:00", "c1")],
        readings=[Reading(decision="unrelated")],
    )
    await say(brain, BOOK_LUNCH)
    assert await say(brain, "What's the weather?") == "Sunny, sir. Lunch remains unbooked."
    assert google.created == []
    messages = (await brain.agent.aget_state(brain.config())).values["messages"]
    dropped = next(m for m in messages if isinstance(m, ToolMessage))
    assert dropped.text == INTERRUPTED  # Jarvis knows the booking never happened


async def test_a_late_yes_starts_afresh(monkeypatch):
    signed_in()
    brain, google, _, approvals_model = brain_with(
        monkeypatch,
        jarvis_says=[ASKS_CALENDAR, AIMessage("Shall I try again, sir?")],
        calendar_says=[proposes_lunch("2026-10-05T12:00", "c1")],
        readings=[Reading(decision="approve")],
    )
    await say(brain, BOOK_LUNCH)
    six_minutes_ago = brain.question.asked_at - timedelta(minutes=6)
    brain.question = replace(brain.question, asked_at=six_minutes_ago)

    assert await say(brain, "Yes") == "Shall I try again, sir?"
    assert google.created == []
    assert approvals_model.read_requests == []  # too late to count as an answer at all


async def test_a_question_cut_off_mid_way_cannot_be_answered(monkeypatch):
    signed_in()
    brain, _, _, _ = brain_with(
        monkeypatch,
        jarvis_says=[ASKS_CALENDAR],
        calendar_says=[proposes_lunch("2026-10-05T12:00", "c1")],
    )
    turn = brain.turn(BOOK_LUNCH)
    first_word = await anext(turn)
    await turn.aclose()  # Ctrl+C (or "stop") before the question finished
    assert first_word == "Shall"
    assert brain.question is None


async def test_reading_needs_no_approval_and_jarvis_hears_only_the_report(monkeypatch):
    signed_in()
    asks_for_today = AIMessage(
        "", tool_calls=[ToolCall(name="calendar_agent", args={"request": "My day?"}, id="j1")]
    )
    lists_today = AIMessage(
        "",
        tool_calls=[ToolCall(name="list_events", args={"start_date": "2026-10-05"}, id="l1")],
    )
    report = "Standup at 9:30, then the dentist at 2."
    brain, _, _, _ = brain_with(
        monkeypatch,
        jarvis_says=[asks_for_today, AIMessage("The dentist at two, sir.")],
        calendar_says=[lists_today, AIMessage(report)],
    )
    events = []
    brain.events.subscribe(events.append)

    assert await say(brain, "What's my day?") == "The dentist at two, sir."
    assert events == [Event("agent_started", {"agent": "calendar"})]
    messages = (await brain.agent.aget_state(brain.config())).values["messages"]
    assert messages[2].text == report
    assert "Main Dental" not in str(messages)  # the raw events stay with the Calendar agent


async def test_not_signed_in_shows_the_button_instead_of_starting_the_agent(monkeypatch):
    brain, _, calendar_model, _ = brain_with(
        monkeypatch,
        jarvis_says=[ASKS_CALENDAR, AIMessage("You'll need to sign in first, sir.")],
        calendar_says=[],
    )
    events = []
    brain.events.subscribe(events.append)

    assert await say(brain, BOOK_LUNCH) == "You'll need to sign in first, sir."
    assert events == [Event("sign_in_needed", {"service": "google"})]
    assert calendar_model.seen == []  # the Calendar agent never started
    messages = (await brain.agent.aget_state(brain.config())).values["messages"]
    assert messages[2].text == SIGN_IN_FIRST


def test_every_tool_that_changes_the_calendar_needs_approval():
    changes = {tool.name for tool in TOOLS} - READ_TOOLS
    assert changes == set(WRITE_TOOLS) == {"create_event", "update_event"}


def test_the_calendar_agent_cannot_delete():
    assert not any("delete" in tool.name or "remove" in tool.name for tool in TOOLS)
