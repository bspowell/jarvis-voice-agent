"""Google Calendar tools for the Calendar agent: read every calendar you see; add, change and
move events.

Plain HTTPS calls to Google's Calendar API, signed with the access token from your Google
sign-in (`auth/google.py`). There's no delete tool. The two write tools never run on the
model's say-so: the Calendar agent pauses before each one until you approve
(`agents/calendar.py`), and `describe_new_event` / `describe_event_change` below say exactly
what you're approving. A change that changes nothing is never put to you (`ask_about_change`).

Times the model sends are local wall-clock times ("2026-10-05T12:00"); Google is told the
calendar's time zone, so daylight-saving changes are its problem, not the model's.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from langchain.agents.middleware import AgentState, ToolCallRequest
from langchain.tools import tool
from langchain_core.messages import AnyMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime

from jarvis.auth.google import NotSignedIn, access_token
from jarvis.config import get_settings

API = "https://www.googleapis.com/calendar/v3"
TIMEOUT_SECONDS = 10.0
MAX_EVENTS = 50  # a day or a week rarely holds more; keeps the agent's reading short
DEFAULT_LENGTH = timedelta(hours=1)  # an event with no end time
ONE_DAY = timedelta(days=1)
MAIN = "main"  # what the tools call your primary calendar
NOTHING_TO_CHANGE = (
    "Nothing to change: pass a new title, start, end, location or description, or "
    "move_to_calendar to put it on another calendar."
)
WRITABLE = {"owner", "writer", "writerWithoutPrivateAccess"}


# --- Tools (what the Calendar agent's model sees) ---
@tool(parse_docstring=True)
async def list_events(
    start_date: str, end_date: str | None = None, search: str | None = None
) -> dict[str, Any]:
    """List the user's events between two dates, soonest first, from every calendar they see,
    and the exact names of the calendars that can be changed.

    Args:
        start_date: First day, e.g. "2026-10-05".
        end_date: Last day, included. Leave out for a single day.
        search: Only events matching these words, e.g. "dentist".
    """
    async with calendar_client() as calendar:
        return await find_events(calendar, start_date, end_date, search)


@tool(parse_docstring=True)
async def create_event(
    title: str,
    start: str,
    end: str | None = None,
    calendar: str | None = None,
    location: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Add an event. The user is asked to confirm before anything is saved.

    Args:
        title: Short name, e.g. "Lunch with Sam".
        start: Local start time, e.g. "2026-10-05T12:00", or a date ("2026-10-05") for an
            all-day event.
        end: Local end time, or the last day of an all-day event. Leave out for one hour
            (one day if all-day).
        calendar: The calendar's name, as list_events shows it. Leave out for the main one.
        location: Where, if the user said.
        description: Notes, if the user gave any.
    """
    async with calendar_client() as client:
        return await add_event(client, title, start, end, calendar, location, description)


@tool(parse_docstring=True)
async def update_event(
    event_id: str,
    calendar: str | None = None,
    move_to_calendar: str | None = None,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Change an existing event: only the fields you pass change. The user confirms first.

    Args:
        event_id: The event's id, from list_events.
        calendar: The calendar it's on now, as list_events shows it. Leave out for the main one.
        move_to_calendar: Another calendar to put it on, by the name list_events shows.
        title: New name.
        start: New local start, e.g. "2026-10-05T15:00". Alone, it moves the event and keeps
            its length.
        end: New local end.
        location: New place.
        description: New notes.
    """
    async with calendar_client() as client:
        return await change_event(
            client,
            event_id,
            calendar,
            title,
            start,
            end,
            location,
            description,
            move_to=move_to_calendar,
        )


# --- The Calendar API ---
class GoogleCalendar:
    """The handful of Calendar API calls Jarvis needs, signed with your access token."""

    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self.http = http
        self.headers = {"Authorization": f"Bearer {token}"}

    async def call(self, method: str, path: str, **options: Any) -> dict[str, Any]:
        response = await self.http.request(method, API + path, headers=self.headers, **options)
        if response.status_code == 401:  # e.g. you removed Jarvis's access mid-request
            raise NotSignedIn("Google didn't accept Jarvis's access token: it may have expired.")
        response.raise_for_status()
        return response.json()

    async def calendars(self) -> list[dict[str, Any]]:
        """Every calendar in your list: yours, shared ones, holidays…"""
        found = await self.call("GET", "/users/me/calendarList", params={"maxResults": 250})
        return found.get("items", [])

    async def events(
        self, calendar_id: str, time_min: str, time_max: str, time_zone: str, search: str | None
    ) -> list[dict[str, Any]]:
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": time_zone,  # report times in your zone
            "singleEvents": "true",  # each repeat of a recurring event, not the series
            "orderBy": "startTime",
            "maxResults": MAX_EVENTS + 1,
        }
        if search:
            params["q"] = search
        found = await self.call(
            "GET", f"/calendars/{quote(calendar_id, safe='')}/events", params=params
        )
        return found.get("items", [])

    async def event(self, calendar_id: str, event_id: str) -> dict[str, Any] | None:
        """One event, or None if there's no such event."""
        path = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        try:
            return await self.call("GET", path)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (404, 410):  # unknown or deleted
                return None
            raise

    async def create(self, calendar_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return await self.call(
            "POST", f"/calendars/{quote(calendar_id, safe='')}/events", json=event
        )

    async def update(
        self, calendar_id: str, event_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """PATCH: Google changes only the fields sent, so nothing else can be touched by mistake."""
        path = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        return await self.call("PATCH", path, json=changes)

    async def move(self, calendar_id: str, event_id: str, destination_id: str) -> dict[str, Any]:
        """Move an event to another calendar, whole: Google keeps its times, guests and notes."""
        path = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}/move"
        return await self.call("POST", path, params={"destination": destination_id})


@asynccontextmanager
async def calendar_client() -> AsyncIterator[GoogleCalendar]:
    """A Calendar API client for one tool call, signed in as you."""
    token = await access_token(get_settings())  # renewed silently if its hour is up
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
        yield GoogleCalendar(http, token)


# --- Reading ---
async def find_events(
    calendar: GoogleCalendar, start_date: str, end_date: str | None, search: str | None
) -> dict[str, Any]:
    """Compact facts: each event's id, title, time in words, calendar and place."""
    try:
        first = date.fromisoformat(start_date)
        last = date.fromisoformat(end_date) if end_date else first
    except ValueError:
        return {"error": "Dates must look like 2026-10-05."}
    if last < first:
        return {"error": "end_date is before start_date."}
    calendars = await calendar.calendars()
    zone = ZoneInfo(main_time_zone(calendars))
    time_min = datetime.combine(first, time(), zone).isoformat()
    time_max = datetime.combine(last + ONE_DAY, time(), zone).isoformat()
    shown = shown_calendars(calendars)
    found = await asyncio.gather(
        *(calendar.events(c["id"], time_min, time_max, zone.key, search) for c in shown)
    )
    events = sorted(
        (
            (starts_at(event, zone), compact_event(event, source, zone))
            for source, items in zip(shown, found, strict=True)
            for event in items
            if worth_listing(event)
        ),
        key=lambda pair: pair[0],
    )
    result: dict[str, Any] = {"time_zone": zone.key, "events": [e for _, e in events[:MAX_EVENTS]]}
    if len(events) > MAX_EVENTS:
        result["more_not_shown"] = len(events) - MAX_EVENTS
    # Names to write with: the user says "the vacation one", Google knows "Jamie + Alex Vacation…"
    result["calendars_you_can_change"] = writable_names(shown)
    return result


def shown_calendars(calendars: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The calendars ticked in Google Calendar's sidebar, whose events you can read."""
    return [
        c
        for c in calendars
        if c.get("selected") and not c.get("hidden") and c.get("accessRole") != "freeBusyReader"
    ]


def worth_listing(event: dict[str, Any]) -> bool:
    """Skips cancelled events, "working from…" markers, and invitations you declined."""
    if event.get("status") == "cancelled" or event.get("eventType") == "workingLocation":
        return False
    you = next((a for a in event.get("attendees", []) if a.get("self")), {})
    return you.get("responseStatus") != "declined"


def compact_event(event: dict[str, Any], source: dict[str, Any], zone: ZoneInfo) -> dict[str, Any]:
    start, end = moment(event["start"], zone), moment(event["end"], zone)
    if not isinstance(start, datetime):
        end -= ONE_DAY  # Google ends all-day events the day after; people say the last day
    found = {
        "id": event["id"],
        "title": event.get("summary", "(no title)"),
        "when": describe_when(start, end),
        "calendar": calendar_name(source),
    }
    if event.get("location"):
        found["location"] = event["location"]
    return found


def moment(field: dict[str, str], zone: ZoneInfo) -> date | datetime:
    """Google's start/end field: {"date": …} for all-day, {"dateTime": …} for a set time."""
    if "dateTime" in field:
        when = datetime.fromisoformat(field["dateTime"])
        return when.astimezone(zone) if when.tzinfo else when.replace(tzinfo=zone)
    return date.fromisoformat(field["date"])


def starts_at(event: dict[str, Any], zone: ZoneInfo) -> datetime:
    """For sorting: all-day events count from midnight."""
    start = moment(event["start"], zone)
    return start if isinstance(start, datetime) else datetime.combine(start, time(), zone)


# --- Calendars by name ---
def calendar_name(entry: dict[str, Any]) -> str:
    if entry.get("primary"):
        return MAIN
    return entry.get("summaryOverride") or entry.get("summary") or entry["id"]


def writable_names(calendars: Sequence[dict[str, Any]]) -> list[str]:
    return [calendar_name(c) for c in calendars if c.get("accessRole") in WRITABLE]


def find_calendar(calendars: Sequence[dict[str, Any]], name: str | None) -> dict[str, Any] | None:
    """A calendar by the name list_events shows; no name (or "main") means your primary one."""
    wanted = (name or MAIN).strip().lower()
    if wanted in (MAIN, "primary"):
        return next((c for c in calendars if c.get("primary")), None)
    for entry in calendars:
        names = {calendar_name(entry), entry.get("summary", ""), entry["id"]}
        if wanted in {n.lower() for n in names}:
            return entry
    return None


def main_time_zone(calendars: Sequence[dict[str, Any]]) -> str:
    main = find_calendar(calendars, MAIN) or {}
    return main.get("timeZone") or "UTC"


def writable_calendar(
    calendars: Sequence[dict[str, Any]], name: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """The calendar to write to, or None and the reason it can't be used."""
    entry = find_calendar(calendars, name)
    if entry is None:
        names = ", ".join(writable_names(shown_calendars(calendars)))
        return None, f"No calendar called {name!r}. Calendars you can change: {names}."
    if entry.get("accessRole") not in WRITABLE:
        return None, f"The {calendar_name(entry)!r} calendar is read-only."
    return entry, None


# --- Writing (runs only after the user approves) ---
async def add_event(
    calendar: GoogleCalendar,
    title: str,
    start: str,
    end: str | None,
    calendar_label: str | None,
    location: str | None,
    description: str | None,
) -> dict[str, Any]:
    calendars = await calendar.calendars()
    target, problem = writable_calendar(calendars, calendar_label)
    if target is None:
        return {"error": problem}
    zone = ZoneInfo(target.get("timeZone") or main_time_zone(calendars))
    try:
        times = event_times(start, end, zone.key)
    except ValueError as error:
        return {"error": str(error)}
    event = {"summary": title, **times}
    if location:
        event["location"] = location
    if description:
        event["description"] = description
    created = await calendar.create(target["id"], event)
    return {"created": compact_event(created, target, zone)}


async def change_event(
    calendar: GoogleCalendar,
    event_id: str,
    calendar_label: str | None,
    title: str | None,
    start: str | None,
    end: str | None,
    location: str | None,
    description: str | None,
    *,
    move_to: str | None = None,
) -> dict[str, Any]:
    calendars = await calendar.calendars()
    source, problem = writable_calendar(calendars, calendar_label)
    if source is None:
        return {"error": problem}
    current = await calendar.event(source["id"], event_id)
    if current is None:
        return {
            "error": f"No event with id {event_id!r} on the {calendar_name(source)!r} calendar."
        }
    target = source
    if move_to:
        target, problem = writable_calendar(calendars, move_to)
        if target is None:
            return {"error": problem}
    zone = ZoneInfo(target.get("timeZone") or main_time_zone(calendars))
    changes: dict[str, Any] = {}
    if title:
        changes["summary"] = title
    if location:
        changes["location"] = location
    if description:
        changes["description"] = description
    if start or end:
        try:
            changes |= new_times(current, start, end, zone)
        except ValueError as error:
            return {"error": str(error)}
    moving = target["id"] != source["id"]
    if not changes and not moving:
        return {"error": NOTHING_TO_CHANGE}
    updated = current
    if moving:  # first, so a new time is set where the event now lives
        updated = await calendar.move(source["id"], event_id, target["id"])
    if changes:
        updated = await calendar.update(target["id"], updated["id"], changes)
    return {"updated": compact_event(updated, target, zone)}


def new_times(
    current: dict[str, Any], start: str | None, end: str | None, zone: ZoneInfo
) -> dict[str, Any]:
    """New start/end fields. A new start alone keeps the length; a new end alone keeps the start."""
    old_first, old_last = local_span(current, zone)
    if start and not end:
        first = parse_moment(start)
        if type(first) is type(old_first):  # same kind (both timed, or both all-day)
            end = (first + (old_last - old_first)).isoformat()
    elif end and not start:
        start = old_first.isoformat()
    return event_times(start or "", end, zone.key)


def local_span(event: dict[str, Any], zone: ZoneInfo) -> tuple[date | datetime, date | datetime]:
    """An event's first and last moment as you'd say them: local times, or inclusive days."""
    start, end = moment(event["start"], zone), moment(event["end"], zone)
    if isinstance(start, datetime) and isinstance(end, datetime):
        return start.replace(tzinfo=None), end.replace(tzinfo=None)
    return start, end - ONE_DAY


def parse_moment(text: str) -> date | datetime:
    """ "2026-10-05" → a day (all-day); "2026-10-05T12:00" → a local time. Raises ValueError.

    Times are wall-clock times in the calendar's zone, so a stray offset ("-07:00") is dropped.
    """
    try:
        if "T" in text:
            return datetime.fromisoformat(text).replace(tzinfo=None)
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"Couldn't read {text!r}: use 2026-10-05T12:00, or 2026-10-05 for all day."
        ) from None


def span(start: str, end: str | None) -> tuple[date | datetime, date | datetime]:
    """First and last moment of a planned event, with the defaults filled in."""
    first = parse_moment(start)
    if isinstance(first, datetime):
        last = parse_moment(end) if end else first + DEFAULT_LENGTH
        if not isinstance(last, datetime):
            raise ValueError("Give the end as a time too, e.g. 2026-10-05T13:00.")
        if last <= first:
            raise ValueError("The end must be after the start.")
        return first, last
    last_day = parse_moment(end) if end else first
    if isinstance(last_day, datetime):
        raise ValueError("For an all-day event, give the last day as a date, e.g. 2026-10-06.")
    if last_day < first:
        raise ValueError("The last day can't be before the first.")
    return first, last_day


def event_times(start: str, end: str | None, time_zone: str) -> dict[str, Any]:
    """Google's start/end fields for a new or moved event."""
    first, last = span(start, end)
    if isinstance(first, datetime):
        return {
            "start": {"dateTime": first.isoformat(), "timeZone": time_zone},
            "end": {"dateTime": last.isoformat(), "timeZone": time_zone},
        }
    # All-day: Google wants the day after the last day.
    return {"start": {"date": first.isoformat()}, "end": {"date": (last + ONE_DAY).isoformat()}}


# --- Times in words (for reports and confirmations) ---
def describe_when(start: date | datetime, end: date | datetime) -> str:
    """Speakable times: "Mon 5 Oct 2026, 2 PM to 3 PM" or "Mon 5 Oct 2026, all day"."""
    if isinstance(start, datetime) and isinstance(end, datetime):
        if start.date() == end.date():
            return f"{day(start)}, {clock(start)} to {clock(end)}"
        return f"{day(start)}, {clock(start)} to {day(end)}, {clock(end)}"
    if start == end:
        return f"{day(start)}, all day"
    return f"{day(start)} to {day(end)}, all day"


def day(moment: date) -> str:
    return f"{moment:%a} {moment.day} {moment:%b %Y}"


def clock(moment: datetime) -> str:
    return f"{moment:%-I %p}" if moment.minute == 0 else f"{moment:%-I:%M %p}"


def planned_when(start: str, end: str | None) -> str:
    """A planned event's time in words; the raw text if it doesn't parse (the tool refuses it)."""
    try:
        return describe_when(*span(start, end))
    except ValueError:
        return f"{start} to {end}" if end else start


def moment_words(text: str) -> str:
    try:
        when = parse_moment(text)
    except ValueError:
        return text
    return f"{day(when)}, {clock(when)}" if isinstance(when, datetime) else day(when)


# --- What you're approving (the confirmation step reads these; see agents/calendar.py) ---
def describe_new_event(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    """create_event, in exact words: built by code from the arguments, so it can't drift."""
    args = tool_call["args"]
    calendar = f"your {args['calendar']} calendar" if args.get("calendar") else "your calendar"
    when = planned_when(args.get("start", ""), args.get("end"))
    text = f"Add “{args.get('title', '(no title)')}” to {calendar}: {when}"
    if args.get("location"):
        text += f", at {args['location']}"
    return text + "."


def describe_event_change(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    """update_event, in exact words. Names the event as list_events showed it in this request."""
    args = tool_call["args"]
    listed = listed_event(state["messages"], args.get("event_id", ""))
    target = f"“{listed['title']}” ({listed['when']})" if listed else "an event"
    return f"Change {target}: {'; '.join(requested_changes(args, listed))}."


def ask_about_change(request: ToolCallRequest) -> bool:
    """Ask only about a change that changes something. An empty one would be refused anyway,
    and a question about nothing invites the asker to guess (once, it guessed "delete").
    (Safe as a `when` check: the arguments can't change between asking and running.)"""
    return bool(requested_changes(request.tool_call["args"]))


def requested_changes(args: dict[str, Any], listed: dict[str, Any] | None = None) -> list[str]:
    """What update_event's arguments change, in words; empty if nothing."""
    changes = []
    if args.get("move_to_calendar"):
        source = f" from your {listed['calendar']} calendar" if listed else ""
        changes.append(f"move it{source} to your {args['move_to_calendar']} calendar")
    if args.get("title"):
        changes.append(f"rename it “{args['title']}”")
    if args.get("start") and args.get("end"):
        changes.append(f"move it to {planned_when(args['start'], args['end'])}")
    elif args.get("start"):
        changes.append(f"move it to start {moment_words(args['start'])}, same length")
    elif args.get("end"):
        changes.append(f"make it end {moment_words(args['end'])}")
    if args.get("location"):
        changes.append(f"set the place to {args['location']}")
    if args.get("description"):
        changes.append("replace its notes")
    return changes


def listed_event(messages: Sequence[AnyMessage], event_id: str) -> dict[str, Any] | None:
    """The event as list_events showed it earlier in this request, or None."""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == list_events.name:
            try:
                events = json.loads(message.text).get("events", [])
            except ValueError:
                continue
            for event in events:
                if event.get("id") == event_id:
                    return event
    return None
