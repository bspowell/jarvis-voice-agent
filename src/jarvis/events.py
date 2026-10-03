"""Jarvis's event bus: tools announce what's happening; each front end decides how to show it.

Example: the search tool announces "the search agent started". Text chat prints
"Right away, sir."; the voice agent (Phase 7) will play it as cached audio. Events carry
facts, never wording, so the same event works for chat, voice and the overlay.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    name: str  # what happened, e.g. "agent_started"
    details: dict[str, Any] = field(default_factory=dict)  # e.g. {"agent": "search"}


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
