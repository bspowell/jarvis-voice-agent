import json

import httpx
import pytest
from fakes import HOLIDAYS, MAIN_CALENDAR, WORK, ZONE, FakeGoogleCalendar
from langchain_core.messages import ToolCall, ToolMessage

from jarvis.auth.google import NotSignedIn
from jarvis.tools.google_calendar import (
    GoogleCalendar,
    add_event,
    change_event,
    describe_event_change,
    describe_new_event,
    find_events,
)


async def test_a_day_lists_every_ticked_calendar_soonest_first():
    calendar = FakeGoogleCalendar()
    found = await find_events(calendar, "2026-10-05", None, None)
    assert found == {
        "time_zone": ZONE,
        "events": [
            {
                "id": "ev-standup",
                "title": "Standup",
                "when": "Mon 5 Oct 2026, 9:30 AM to 9:45 AM",
                "calendar": "Work",
            },
            {
                "id": "ev-dentist",
                "title": "Dentist",
                "when": "Mon 5 Oct 2026, 2 PM to 3 PM",
                "calendar": "main",
                "location": "Main Dental",
            },
        ],
    }
    # Declined invitations and "working from" markers are skipped; unticked calendars aren't read.
    assert {read[0] for read in calendar.reads} == {MAIN_CALENDAR["id"], WORK["id"], HOLIDAYS["id"]}


async def test_a_day_runs_midnight_to_midnight_in_your_time_zone():
    calendar = FakeGoogleCalendar()
    await find_events(calendar, "2026-10-05", None, None)
    _calendar, time_min, time_max, _search = calendar.reads[0]
    assert (time_min, time_max) == ("2026-10-05T00:00:00-07:00", "2026-10-06T00:00:00-07:00")


async def test_a_week_includes_all_day_events():
    found = await find_events(FakeGoogleCalendar(), "2026-10-05", "2026-10-12", None)
    assert found["events"][-1] == {
        "id": "ev-thanksgiving",
        "title": "Thanksgiving",
        "when": "Mon 12 Oct 2026, all day",  # Google's end date is the day after; we say the day
        "calendar": "Holidays in Canada",
    }


async def test_unreadable_dates_are_a_fact_for_the_agent():
    found = await find_events(FakeGoogleCalendar(), "next Tuesday", None, None)
    assert found == {"error": "Dates must look like 2026-10-05."}


async def test_a_new_event_goes_to_the_main_calendar_for_an_hour():
    calendar = FakeGoogleCalendar()
    result = await add_event(calendar, "Lunch with Sam", "2026-10-05T12:00", None, None, None, None)
    assert calendar.created == [
        (
            MAIN_CALENDAR["id"],
            {
                "summary": "Lunch with Sam",
                # Local times plus the calendar's zone: Google handles daylight saving.
                "start": {"dateTime": "2026-10-05T12:00:00", "timeZone": ZONE},
                "end": {"dateTime": "2026-10-05T13:00:00", "timeZone": ZONE},
            },
        )
    ]
    assert result["created"]["when"] == "Mon 5 Oct 2026, 12 PM to 1 PM"


async def test_an_all_day_event_ends_the_day_after():
    calendar = FakeGoogleCalendar()
    await add_event(calendar, "Farmers market", "2026-10-09", None, None, None, None)
    [(_calendar, event)] = calendar.created
    assert (event["start"], event["end"]) == ({"date": "2026-10-09"}, {"date": "2026-10-10"})


async def test_a_calendar_is_found_by_the_name_list_events_shows():
    calendar = FakeGoogleCalendar()
    await add_event(
        calendar, "Planning", "2026-10-06T10:00", "2026-10-06T10:30", "work", None, None
    )
    assert calendar.created[0][0] == WORK["id"]


@pytest.mark.parametrize(
    ("name", "problem"),
    [("Holidays in Canada", "read-only"), ("Gym", "No calendar called 'Gym'")],
)
async def test_writes_need_a_calendar_you_can_write_to(name, problem):
    calendar = FakeGoogleCalendar()
    result = await add_event(calendar, "Tennis", "2026-10-05T18:00", None, name, None, None)
    assert problem in result["error"]
    assert calendar.created == []


async def test_an_end_before_the_start_is_refused():
    calendar = FakeGoogleCalendar()
    result = await add_event(
        calendar, "Tennis", "2026-10-05T18:00", "2026-10-05T17:00", None, None, None
    )
    assert result == {"error": "The end must be after the start."}
    assert calendar.created == []


async def test_a_change_touches_only_the_fields_named():
    calendar = FakeGoogleCalendar()
    result = await change_event(
        calendar, "ev-dentist", None, None, "2026-10-05T15:00", None, None, None
    )
    # Moved to 3 PM: the end follows, keeping its hour. Title and place aren't sent at all.
    assert calendar.updated == [
        (
            MAIN_CALENDAR["id"],
            "ev-dentist",
            {
                "start": {"dateTime": "2026-10-05T15:00:00", "timeZone": ZONE},
                "end": {"dateTime": "2026-10-05T16:00:00", "timeZone": ZONE},
            },
        )
    ]
    assert result["updated"]["when"] == "Mon 5 Oct 2026, 3 PM to 4 PM"
    assert result["updated"]["title"] == "Dentist"


async def test_changing_an_unknown_event_is_a_fact():
    calendar = FakeGoogleCalendar()
    result = await change_event(calendar, "ev-nope", None, "New name", None, None, None, None)
    assert "No event with id 'ev-nope'" in result["error"]
    assert calendar.updated == []


def test_the_confirmation_says_exactly_what_will_be_added():
    args = {"title": "Lunch with Sam", "start": "2026-10-05T12:00", "location": "Taco Hut"}
    call = ToolCall(name="create_event", args=args, id="c1")
    assert describe_new_event(call, {"messages": []}, None) == (
        "Add “Lunch with Sam” to your calendar: Mon 5 Oct 2026, 12 PM to 1 PM, at Taco Hut."
    )


def test_the_confirmation_names_the_event_being_changed():
    listed = {"id": "ev-dentist", "title": "Dentist", "when": "Mon 5 Oct 2026, 2 PM to 3 PM"}
    listing = ToolMessage(json.dumps({"events": [listed]}), tool_call_id="l1", name="list_events")
    args = {"event_id": "ev-dentist", "start": "2026-10-05T15:00"}
    call = ToolCall(name="update_event", args=args, id="u1")
    assert describe_event_change(call, {"messages": [listing]}, None) == (
        "Change “Dentist” (Mon 5 Oct 2026, 2 PM to 3 PM): "
        "move it to start Mon 5 Oct 2026, 3 PM, same length."
    )


async def test_calls_reach_the_calendar_api_signed_in_and_safely_encoded():
    seen: list[httpx.Request] = []

    def google(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"id": "ev-1", **json.loads(request.content)})

    http = httpx.AsyncClient(transport=httpx.MockTransport(google))
    calendar = GoogleCalendar(http, token="access-1")
    await calendar.events(HOLIDAYS["id"], "2026-10-05T00:00:00-07:00", "x", ZONE, "turkey")
    await calendar.update(MAIN_CALENDAR["id"], "ev-1", {"summary": "Dentist"})

    listing, change = seen
    assert listing.headers["Authorization"] == "Bearer access-1"
    # "#" and "@" in calendar ids must be encoded, or the address would break.
    assert "/calendars/en.canadian%23holiday%40group.v.calendar.google.com/events" in str(
        listing.url
    )
    assert listing.url.params["singleEvents"] == "true"  # each repeat of a recurring event
    assert listing.url.params["q"] == "turkey"
    assert (change.method, json.loads(change.content)) == ("PATCH", {"summary": "Dentist"})


async def test_an_event_that_does_not_exist_is_none():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert await GoogleCalendar(http, token="access-1").event("primary", "ev-gone") is None


async def test_a_refused_token_means_not_signed_in():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(NotSignedIn, match="may have expired"):
        await GoogleCalendar(http, token="access-old").calendars()
