from datetime import datetime

import pytest

from jarvis.brain.greeting import boot_greeting, part_of_day


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (2, "evening"),
    ],
)
def test_part_of_day(hour, expected):
    assert part_of_day(datetime(2026, 10, 3, hour)) == expected


def test_greeting_with_full_roster():
    assert boot_greeting(datetime(2026, 10, 3, 9), agents_present=3) == (
        "Good morning, sir. Three Agents all present and accounted for. "
        "What would you like to do today?"
    )


def test_greeting_before_any_agents_exist():
    greeting = boot_greeting(datetime(2026, 10, 3, 20), agents_present=0)
    assert greeting.startswith("Good evening, sir.")
    assert greeting.endswith("What would you like to do today?")
