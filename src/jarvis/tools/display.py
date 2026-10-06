"""The display tool: Jarvis shows, hides or moves his orb, the ring of light on your screen.

The Brain can't reach the desktop app by itself: whoever is talking to Jarvis hands it a way to,
in the runtime context (brain/context.py). The voice agent hands over a function that asks the
desktop app over LiveKit; text chat hands over none, as there's no orb there. The tool's result
is what the desktop app says it did, so Jarvis never claims a change that didn't happen.
"""

from langchain.tools import ToolRuntime, tool

from jarvis.brain.context import JarvisContext
from jarvis.display import Action, Core, Corner, DisplayCommand, Screen

NO_ORB = "No orb here: it's only on screen while the user talks to you through the desktop app."


@tool(parse_docstring=True)
async def display_orb(
    action: Action,
    runtime: ToolRuntime[JarvisContext],
    corner: Corner | None = None,
    screen: Screen = "same",
    core: Core | None = None,
) -> str:
    """Show, hide or move your orb (the ring of light on the user's screen), or change what's in
    its middle.

    Args:
        action: "hide" puts it out of sight, "show" brings it back, "move" moves it, "switch"
            changes its centrepiece.
        corner: Where to move it.
        screen: "other" moves it to the user's other screen, if they have one.
        core: For "switch": jarvis (the classic J.A.R.V.I.S. rings with your name); Thor's
            weapons: mjolnir (his hammer) or stormbreaker (his axe); or one of Iron Man's arc
            reactors: iron-man-1 or infinity-war (the nano one).
    """
    display = runtime.context.display if runtime.context else None
    if display is None:
        return NO_ORB
    return await display(DisplayCommand(action, corner, screen, core))
