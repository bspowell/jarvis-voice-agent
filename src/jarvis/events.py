"""Jarvis's event bus: tools announce what's happening; each front end decides how to show it.

Example: the search tool announces "the search agent started". Text chat prints
"Right away, sir."; the voice agent says it, from a recording. Events carry
facts, never wording, so the same event works for chat, voice and the overlay.

The events:
- From the Brain's tools: `agent_started` (cues the filler), `sign_in_needed` (the button),
  `approval_needed` (the exact change), and facts worth seeing: `weather`, `agenda`, `tasks`,
  `further_reading` (the desktop draws a card beside the orb).
- From the voice agent, about a sign-in card's button: `signing_in`, `signed_in`,
  `sign_in_failed`.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    name: str  # what happened, e.g. "agent_started"
    details: dict[str, Any] = field(default_factory=dict)  # e.g. {"agent": "search"}

    def to_json(self) -> str:
        """As text, for the room: the voice agent passes events on to the desktop app."""
        return json.dumps({"name": self.name, "details": self.details})

    @classmethod
    def from_json(cls, text: str) -> "Event":
        """Raises ValueError for anything that isn't an event."""
        fields = json.loads(text)
        if not isinstance(fields, dict) or not isinstance(fields.get("name"), str):
            raise ValueError(f"Not an event: {text[:80]}")
        details = fields.get("details", {})
        if not isinstance(details, dict):
            raise ValueError(f"Not an event: {text[:80]}")
        return cls(fields["name"], details)


class EventBus:
    """Hands every published event to every subscriber, in the order they subscribed."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[Event], None]] = []

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        """`handler` runs for each event. Keep it quick: it runs while Jarvis is mid-turn."""
        self._subscribers.append(handler)

    def publish(self, event: Event) -> None:
        for handler in self._subscribers:
            handler(event)
