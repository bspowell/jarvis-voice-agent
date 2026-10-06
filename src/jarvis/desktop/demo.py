"""`jarvis desktop --demo`: the orb shows every look in turn, with a pretend voice, then again
with the next centrepiece. No LiveKit, no Jarvis: for seeing (and tuning) the orb on your own
screen, over your own windows.

It opens with the cards: they arrive as his tools would finish, then fade once the conversation
is over. The sign-in card stays: click it, and it goes through its steps (no browser)."""

import asyncio
import itertools
import logging
import random
import threading
from datetime import datetime
from typing import get_args

from jarvis.desktop.room_client import LEVELS_PER_SECOND, Orb
from jarvis.display import CORE_NAMES, Core
from jarvis.events import Event

logger = logging.getLogger(__name__)

LOOKS = [  # what to show, as the room would report it, and for how long (seconds)
    ("Away: not connected", {"state": "away", "awake": False}, 3.0),
    ("Asleep: waiting for “Hey Jarvis”", {"state": "listening", "awake": False}, 4.0),
    ("Listening to you", {"state": "listening", "awake": True}, 3.0),
    ("Thinking", {"state": "thinking", "awake": True}, 3.0),
    ("Speaking", {"state": "speaking", "awake": True}, 6.0),
    ("Listening for a follow-up", {"state": "listening", "awake": True}, 2.0),
]


async def show_off(orb: Orb) -> None:
    """The cards, then every look in turn, with each centrepiece in turn, until hung up."""
    await show_cards(orb)
    for core in itertools.cycle(get_args(Core)):
        logger.info("Orb: %s in the middle", CORE_NAMES[core])
        orb.update(core=core)
        for name, fields, seconds in LOOKS:
            logger.info("Orb: %s", name)
            orb.update(**fields, level=0)
            if fields["state"] == "speaking":
                await pretend_voice(orb, seconds)
            else:
                await asyncio.sleep(seconds)


async def pretend_voice(orb: Orb, seconds: float) -> None:
    """Syllables of random loudness, with short pauses between words."""
    left = 0.0
    loudness = 0.0
    for _ in range(round(seconds * LEVELS_PER_SECOND)):
        left -= 1 / LEVELS_PER_SECOND
        if left <= 0:
            pause = random.random() < 0.18
            left = random.uniform(0.25, 0.6) if pause else random.uniform(0.09, 0.25)
            loudness = 0.0 if pause else random.uniform(0.35, 1.0)
        orb.update(level=round(loudness * random.uniform(0.6, 1.0), 2))
        await asyncio.sleep(1 / LEVELS_PER_SECOND)
    orb.update(level=0)


async def show_cards(orb: Orb) -> None:
    """A conversation's cards: they arrive as he thinks, stay while he talks, then fade."""
    logger.info("Cards: they arrive as his tools finish")
    orb.update(state="thinking", awake=True, level=0)
    for event in sample_events(datetime.now()):
        await asyncio.sleep(1.2)
        orb.show_event(event)
    logger.info("Cards: he talks about them")
    orb.update(state="speaking")
    await pretend_voice(orb, 5.0)
    logger.info("Cards: the conversation is over; they fade in 10 s (the sign-in card waits)")
    orb.update(state="listening", awake=False)
    await asyncio.sleep(11.0)


def pretend_sign_in(orb: Orb, service: str) -> None:
    """A click on the demo's "Sign in": the card goes through its steps, with no browser."""
    logger.info("Cards: you clicked “Sign in” (%s)", service)
    orb.show_event(Event("signing_in", {"service": service}))
    signed_in = Event("signed_in", {"service": service})
    threading.Timer(2.5, orb.show_event, [signed_in]).start()


def sample_events(now: datetime) -> list[Event]:
    """What his tools might announce: the weather, today's agenda, links, a sign-in."""
    today = now.date().isoformat()

    def at(hour: int, minute: int = 0) -> str:
        return f"{today}T{hour:02d}:{minute:02d}"

    weather = {
        "place": "Vancouver, British Columbia, CA",
        "now": {"temp_c": 14, "feels_like_c": 12, "conditions": "overcast", "wind_kmh": 18},
        "today": {
            "high_c": 16,
            "low_c": 9,
            "conditions": "light rain",
            "precipitation_chance_pct": 80,
            "precipitation_mm": 4.2,
        },
    }
    day = [
        {"title": "Standup", "start": at(9, 30), "end": at(9, 45), "calendar": "Work"},
        {"title": "Lunch with Sam", "start": at(12), "end": at(13), "location": "Main Café"},
        {"title": "Dentist", "start": at(15, 30), "end": at(16, 30), "location": "Main Dental"},
    ]
    links = [
        ("Releases · langchain-ai/langgraph", "https://github.com/langchain-ai/langgraph/releases"),
        ("langgraph · PyPI", "https://pypi.org/project/langgraph/"),
    ]
    return [
        Event("weather", weather),
        Event("agenda", {"first_day": today, "last_day": today, "search": None, "events": day}),
        Event("further_reading", {"sources": [{"title": t, "url": u} for t, u in links]}),
        Event("sign_in_needed", {"service": "google"}),
    ]
