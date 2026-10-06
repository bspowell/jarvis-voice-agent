"""`jarvis desktop --demo`: the orb shows every look in turn, with a pretend voice, then again
with the next centrepiece. No LiveKit, no Jarvis: for seeing (and tuning) the orb on your own
screen, over your own windows."""

import asyncio
import itertools
import logging
import random
from typing import get_args

from jarvis.desktop.room_client import LEVELS_PER_SECOND, Orb
from jarvis.display import CORE_NAMES, Core

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
    """Every look in turn, with each centrepiece in turn, until hung up."""
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
