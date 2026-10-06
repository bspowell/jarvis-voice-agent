"""What the cards beside the orb say, from the events Jarvis's tools announce."""

from datetime import date, datetime

from jarvis.desktop.cards import EN_DASH, MAX_ROWS, card_for, day_name
from jarvis.events import Event

NOW = datetime(2026, 10, 5, 11, 0)  # Monday 5 October, 11 AM (local, like the agenda's times)
WEATHER = Event(
    "weather",
    {
        "place": "Vancouver, British Columbia, CA",
        "located_by": "ip_address",
        "now": {"temp_c": 14, "feels_like_c": 12, "conditions": "overcast", "wind_kmh": 18},
        "today": {
            "high_c": 16,
            "low_c": 9,
            "conditions": "light rain",
            "precipitation_chance_pct": 80,
            "precipitation_mm": 4.2,
        },
    },
)


def agenda(first: str, last: str, *events: dict, search: str | None = None) -> Event:
    return Event(
        "agenda",
        {"first_day": first, "last_day": last, "search": search, "events": list(events)},
    )


STANDUP = {"title": "Standup", "start": "2026-10-05T09:30", "end": "2026-10-05T09:45"}
LUNCH = {
    "title": "Lunch with Sam",
    "start": "2026-10-05T12:00",
    "end": "2026-10-05T13:00",
    "location": "Main Café",
}
DENTIST = {"title": "Dentist", "start": "2026-10-06T14:00", "end": "2026-10-06T15:00"}
THANKSGIVING = {"title": "Thanksgiving", "start": "2026-10-12", "end": "2026-10-12"}


def test_the_weather_card_leads_with_the_temperature():
    card = card_for(WEATHER, NOW)
    assert card == {
        "id": "weather",
        "kind": "weather",
        "title": "Vancouver",
        "temp": "14°",
        "conditions": "Overcast",
        "feels": "Feels like 12°",
        "low": "9°",
        "high": "16°",
        "at": 0.71,  # 14° sits most of the way from today's low to its high
        "rain": "80%",
        "rain_likely": True,  # shown in amber
        "wind": "18 km/h",
        "later": "Light rain",
    }


def test_todays_agenda_dims_what_is_over_and_marks_what_is_next():
    card = card_for(agenda("2026-10-05", "2026-10-05", STANDUP, LUNCH), NOW)
    assert (card["title"], card["side"]) == ("Today", "Mon 5 Oct")
    [day] = card["days"]
    assert day["label"] is None  # one day: the title already says which
    assert day["events"] == [
        {"time": "9:30 AM", "title": "Standup", "where": None, "past": True, "next": False},
        {
            "time": "12 PM",
            "title": "Lunch with Sam",
            "where": "Main Café",
            "past": False,
            "next": True,
        },
    ]
    assert card["more"] == 0


def test_a_week_is_grouped_by_day():
    week = agenda("2026-10-05", "2026-10-12", LUNCH, DENTIST, THANKSGIVING)
    card = card_for(week, NOW)
    assert (card["title"], card["side"]) == (f"5{EN_DASH}12 Oct", None)
    assert [day["label"] for day in card["days"]] == ["Today", "Tomorrow", "Mon 12 Oct"]
    holiday = card["days"][2]["events"][0]
    assert holiday == {
        "time": "All day",
        "title": "Thanksgiving",
        "where": None,
        "past": False,
        "next": False,  # the next thing to go to is lunch, not a day off
    }


def test_a_search_names_what_was_looked_for_and_long_lists_are_counted():
    found = agenda("2026-10-05", "2026-11-30", *[DENTIST] * (MAX_ROWS + 2), search="dentist")
    card = card_for(found, NOW)
    assert (card["title"], card["side"]) == ("Dentist", f"5 Oct {EN_DASH} 30 Nov")
    assert sum(len(day["events"]) for day in card["days"]) == MAX_ROWS
    assert card["more"] == 2
    assert card_for(agenda("2026-10-05", "2026-10-05"), NOW)["empty"] == "Nothing on."


def test_to_dos_say_when_they_are_due():
    todo = Event(
        "tasks",
        {
            "due_by": None,
            "tasks": [
                {"title": "Call the bank", "due": "2026-10-02", "done": False, "list": "My Tasks"},
                {"title": "Milk", "due": "2026-10-06", "done": False, "list": "Shopping"},
                {"title": "File taxes", "due": "2026-10-01", "done": True, "list": "My Tasks"},
                {"title": "Call the plumber", "due": None, "done": False, "list": "My Tasks"},
            ],
        },
    )
    card = card_for(todo, NOW)
    assert card["tasks"] == [
        {"title": "Call the bank", "due": "Overdue", "overdue": True, "done": False},
        {"title": "Milk", "due": "Tomorrow", "overdue": False, "done": False},
        {"title": "File taxes", "due": "Thu 1 Oct", "overdue": False, "done": True},
        {"title": "Call the plumber", "due": None, "overdue": False, "done": False},
    ]


def test_further_reading_shows_each_pages_site():
    url = "https://www.formula1.com/en/results/2026/races"
    card = card_for(Event("further_reading", {"sources": [{"title": "Results", "url": url}]}), NOW)
    assert card["links"] == [{"title": "Results", "site": "formula1.com", "url": url}]


def test_a_sign_in_card_goes_from_the_button_to_signed_in():
    steps = ["sign_in_needed", "signing_in", "signed_in", "sign_in_failed"]
    cards = [card_for(Event(step, {"service": "google"}), NOW) for step in steps]
    assert {card["id"] for card in cards} == {"sign_in:google"}  # one card, updated in place
    assert [(card["text"], card["button"]) for card in cards] == [
        ("Your calendar and to-dos need a Google sign-in.", "Sign in with Google"),
        ("Finish signing in in your browser.", None),
        ("Signed in.", None),
        ("The sign-in didn't finish.", "Try again"),
    ]
    assert card_for(Event("sign_in_needed", {"service": "myspace"}), NOW) is None


def test_events_without_a_card_get_none():
    assert card_for(Event("agent_started", {"agent": "search"}), NOW) is None


def test_days_are_named_as_people_say_them():
    today = date(2026, 10, 5)  # a Monday
    names = [day_name(date(2026, 10, d), today) for d in (4, 5, 6, 9, 11, 12)]
    assert names == ["Yesterday", "Today", "Tomorrow", "Friday", "Sunday", "Mon 12 Oct"]
