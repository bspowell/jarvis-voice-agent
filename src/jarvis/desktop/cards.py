"""What each card beside the orb says, made from the events Jarvis's tools announce.

Plain Python, no windows: the card panel (card_panel.py) hands each card to the page
(web/cards.js), which lays it out and animates it. Each card has an `id`: one card per kind
(one per account for sign-ins), so a new card replaces the old one.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit

from jarvis.events import Event

Card = dict[str, Any]

MAX_ROWS = 6  # events or to-dos on one card; the rest are counted ("2 more")
EN_DASH = "\u2013"  # for ranges of days
LIKELY_RAIN = 60  # % chance: the rain figure turns amber
SIGN_IN = {  # per account: its name, what it's for, and the button
    "google": ("Google", "Your calendar and to-dos need a Google sign-in.", "Sign in with Google"),
    "notion": ("Notion", "Your Notion notes need a connection.", "Connect Notion"),
}


def card_for(event: Event, now: datetime) -> Card | None:
    """The card an event deserves, ready for the page; None for events without one."""
    make = CARDS.get(event.name)
    return make(event, now) if make else None


# --- The weather ---
def weather_card(event: Event, now: datetime) -> Card:
    report = event.details
    current, today = report["now"], report["today"]
    low, high, temp = today["low_c"], today["high_c"], current["temp_c"]
    return {
        "id": "weather",
        "kind": "weather",
        "title": report["place"].split(",")[0],  # "Vancouver, British Columbia, CA"
        "temp": f"{temp}°",
        "conditions": current["conditions"].capitalize(),
        "feels": f"Feels like {current['feels_like_c']}°",
        "low": f"{low}°",
        "high": f"{high}°",
        "at": share(temp, low, high),  # where now sits between today's low and high, 0 to 1
        "rain": f"{today['precipitation_chance_pct']}%",
        "rain_likely": today["precipitation_chance_pct"] >= LIKELY_RAIN,
        "wind": f"{current['wind_kmh']} km/h",
        "later": today["conditions"].capitalize(),
    }


def share(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return round(min(1.0, max(0.0, (value - low) / (high - low))), 2)


# --- The calendar ---
def agenda_card(event: Event, now: datetime) -> Card:
    """Events by day, with times. Finished events are dimmed; the next one is marked.
    `now` is your local time, like the agenda's (both naive)."""
    agenda = event.details
    first, last = date.fromisoformat(agenda["first_day"]), date.fromisoformat(agenda["last_day"])
    today = now.date()
    rows = agenda["events"][:MAX_ROWS]
    days: list[dict[str, Any]] = []
    upcoming = next((row for row in rows if not all_day(row) and ends(row) > now), None)
    for row in rows:
        day = date.fromisoformat(row["start"][:10])
        if not days or days[-1]["day"] != day:
            days.append({"day": day, "label": day_name(day, today), "events": []})
        days[-1]["events"].append(agenda_row(row, now, row is upcoming))
    if agenda.get("search"):
        title, side = agenda["search"].capitalize(), date_span(first, last)
    elif first == last:
        title, side = day_name(first, today), short_date(first) if is_near(first, today) else None
    else:
        title, side = date_span(first, last), None
    single_day = len(days) == 1 and first == last
    return {
        "id": "agenda",
        "kind": "agenda",
        "title": title,
        "side": side,
        "days": [
            {"label": None if single_day else day["label"], "events": day["events"]} for day in days
        ],
        "more": len(agenda["events"]) - len(rows) + agenda.get("more", 0),
        "empty": "Nothing found." if agenda.get("search") else "Nothing on.",
    }


def agenda_row(row: dict[str, Any], now: datetime, upcoming: bool) -> dict[str, Any]:
    return {
        "time": "All day" if all_day(row) else clock(datetime.fromisoformat(row["start"])),
        "title": row["title"],
        "where": row.get("location"),
        "past": ends(row) <= now,
        "next": upcoming,
    }


def all_day(row: dict[str, Any]) -> bool:
    return len(row["start"]) == len("2026-10-05")  # a day, no time


def ends(row: dict[str, Any]) -> datetime:
    """When an agenda row is over (an all-day event: when its last day is)."""
    if all_day(row):
        return datetime.combine(date.fromisoformat(row["end"]) + timedelta(days=1), time())
    return datetime.fromisoformat(row["end"])


# --- To-dos ---
def tasks_card(event: Event, now: datetime) -> Card:
    todo = event.details
    today = now.date()
    rows = todo["tasks"][:MAX_ROWS]
    due_by = todo.get("due_by")
    return {
        "id": "tasks",
        "kind": "tasks",
        "title": "To-dos",
        "side": f"Due by {day_name(date.fromisoformat(due_by), today)}" if due_by else None,
        "tasks": [task_row(task, today) for task in rows],
        "more": len(todo["tasks"]) - len(rows) + todo.get("more", 0),
        "empty": "Nothing due.",
    }


def task_row(task: dict[str, Any], today: date) -> dict[str, Any]:
    due = date.fromisoformat(task["due"]) if task.get("due") else None
    overdue = due is not None and due < today and not task.get("done")
    return {
        "title": task["title"],
        "due": "Overdue" if overdue else (day_name(due, today) if due else None),
        "overdue": overdue,
        "done": bool(task.get("done")),
    }


# --- Further reading (clickable) ---
def sources_card(event: Event, now: datetime) -> Card:
    return {
        "id": "sources",
        "kind": "sources",
        "title": "Further reading",
        "links": [
            {"title": link["title"], "site": site(link["url"]), "url": link["url"]}
            for link in event.details["sources"]
        ],
    }


def site(url: str) -> str:
    """ "https://www.formula1.com/en/results" → "formula1.com"."""
    host = urlsplit(url).hostname or url
    return host.removeprefix("www.")


# --- Sign-ins (clickable) ---
def sign_in_card(event: Event, now: datetime) -> Card | None:
    """The four steps of a sign-in card: the button; waiting for you in the browser; signed in;
    and didn't finish (the button again)."""
    service = event.details.get("service")
    if service not in SIGN_IN:
        return None
    name, needs, button = SIGN_IN[service]
    text, label = {
        "sign_in_needed": (needs, button),
        "signing_in": ("Finish signing in in your browser.", None),
        "signed_in": ("Signed in.", None),
        "sign_in_failed": ("The sign-in didn't finish.", "Try again"),
    }[event.name]
    return {
        "id": f"sign_in:{service}",
        "kind": "sign_in",
        "service": service,
        "title": name,
        "text": text,
        "button": label,
        "state": event.name,
    }


# --- Days and times, as a card says them ---
def day_name(day: date, today: date) -> str:
    """ "Today", "Tomorrow", "Thursday" (within the week ahead), else "Thu 15 Oct"."""
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    if day == today - timedelta(days=1):
        return "Yesterday"
    if today < day < today + timedelta(days=7):
        return f"{day:%A}"
    return short_date(day)


def is_near(day: date, today: date) -> bool:
    """Is `day` named without its date ("Today", "Thursday")?"""
    return day_name(day, today) != short_date(day)


def short_date(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def date_span(first: date, last: date) -> str:
    """The days a listing covers, joined by an en dash: 6 to 11 Oct, or 28 Oct to 3 Nov."""
    if first.month == last.month:
        return f"{first.day}{EN_DASH}{last.day} {last:%b}"
    return f"{first.day} {first:%b} {EN_DASH} {last.day} {last:%b}"


def clock(moment: datetime) -> str:
    """ "9 AM", "9:30 AM": as Jarvis says times."""
    return f"{moment:%-I %p}" if moment.minute == 0 else f"{moment:%-I:%M %p}"


CARDS: dict[str, Callable[[Event, datetime], Card | None]] = {
    "weather": weather_card,
    "agenda": agenda_card,
    "tasks": tasks_card,
    "further_reading": sources_card,
    "sign_in_needed": sign_in_card,
    "signing_in": sign_in_card,
    "signed_in": sign_in_card,
    "sign_in_failed": sign_in_card,
}
