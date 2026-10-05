"""Jarvis's fixed lines: plain Python, so they're instant and free (no model call needed)."""

from datetime import datetime

FILLER = "Right away, sir."  # while a sub-agent works: chat prints it, voice says it

NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]


def part_of_day(now: datetime) -> str:
    if 5 <= now.hour < 12:
        return "morning"
    if 12 <= now.hour < 17:
        return "afternoon"
    return "evening"


def boot_greeting(now: datetime, agents_present: int) -> str:
    opening = f"Good {part_of_day(now)}, sir."
    question = "What would you like to do today?"
    if agents_present == 0:
        return f"{opening} No agents on duty just yet, but I remain at your service. {question}"
    count = (
        NUMBER_WORDS[agents_present] if agents_present < len(NUMBER_WORDS) else str(agents_present)
    )
    roll_call = "Agent present" if agents_present == 1 else "Agents all present"
    return f"{opening} {count} {roll_call} and accounted for. {question}"
