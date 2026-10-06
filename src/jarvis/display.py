"""The orb's display commands: Jarvis's `display_orb` tool asks, the desktop app does.

They travel tool → voice agent → LiveKit RPC → desktop app, and the desktop's answer (what it
actually did) travels back as the tool's result. Shared by all three, so it imports nothing heavy.
"""

import json
from dataclasses import asdict, dataclass
from typing import Literal, get_args

Action = Literal["show", "hide", "move", "switch"]  # "switch": a new centrepiece
Corner = Literal["bottom right", "bottom left", "top right", "top left"]
Screen = Literal["same", "other"]  # "other": the next display, if you have more than one
Core = Literal["jarvis", "mjolnir", "stormbreaker", "iron-man-1", "infinity-war"]
CORE_NAMES: dict[str, str] = {  # how the desktop app's answers name them
    "jarvis": "the classic J.A.R.V.I.S. rings",
    "mjolnir": "Mjölnir",
    "stormbreaker": "Stormbreaker",
    "iron-man-1": "the Iron Man 1 arc reactor",
    "infinity-war": "the Infinity War nano reactor",
}

DESKTOP = "desktop"  # the desktop app's identity in Jarvis's room
DISPLAY_METHOD = "jarvis.display"  # the RPC method the desktop app answers
AWAKE = "jarvis.awake"  # the voice agent's room attribute: "true" while the wake-word gate is open


@dataclass(frozen=True)
class DisplayCommand:
    action: Action
    corner: Corner | None = None  # for "move"; None keeps the corner
    screen: Screen = "same"
    core: Core | None = None  # for "switch"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "DisplayCommand":
        """Raises ValueError for anything that isn't a valid command."""
        fields = json.loads(text)
        command = cls(
            fields["action"], fields.get("corner"), fields.get("screen", "same"), fields.get("core")
        )
        if (
            command.action not in get_args(Action)
            or command.corner not in (None, *get_args(Corner))
            or command.screen not in get_args(Screen)
            or command.core not in (None, *get_args(Core))
        ):
            raise ValueError(f"Not a display command: {text}")
        return command
