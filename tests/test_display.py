"""The display tool: Jarvis asks, the front end's controls do it, and he hears what was done."""

import pytest
from fakes import FakeToolCallingModel
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from jarvis.brain.brain import Brain
from jarvis.brain.context import JarvisContext
from jarvis.display import DisplayCommand
from jarvis.tools.display import NO_ORB, display_orb

HIDE = AIMessage("", tool_calls=[ToolCall(name="display_orb", args={"action": "hide"}, id="c1")])
MOVE = AIMessage(
    "",
    tool_calls=[
        ToolCall(
            name="display_orb",
            args={"action": "move", "corner": "top left", "screen": "other"},
            id="c2",
        )
    ],
)


def make_brain(*replies: AIMessage | str) -> Brain:
    """A Brain with only the display tool and a scripted model (no API calls)."""
    model = FakeToolCallingModel(messages=iter(replies))
    agent = create_agent(
        model,
        tools=[display_orb],
        context_schema=JarvisContext,
        checkpointer=InMemorySaver(),
        name="jarvis",
    )
    return Brain(agent)


async def say(brain: Brain, text: str) -> str:
    return "".join([chunk async for chunk in brain.turn(text)])


async def tool_result(brain: Brain) -> str:
    state = await brain.agent.aget_state(brain.config())
    return next(m.text for m in state.values["messages"] if isinstance(m, ToolMessage))


async def test_jarvis_hears_what_the_desktop_actually_did():
    brain = make_brain(MOVE, "Top left of the other screen, sir.")
    asked: list[DisplayCommand] = []

    async def desktop(command: DisplayCommand) -> str:
        asked.append(command)
        return "The orb is showing, top left of the LG UltraFine."

    brain.display = desktop  # what the voice agent plugs in
    reply = await say(brain, "Put the orb top left on the other screen.")
    assert asked == [DisplayCommand("move", "top left", "other")]
    assert await tool_result(brain) == "The orb is showing, top left of the LG UltraFine."
    assert reply == "Top left of the other screen, sir."


async def test_in_text_chat_there_is_no_orb_to_change():
    brain = make_brain(HIDE, "There's no orb in here, sir.")
    await say(brain, "Hide the orb.")  # no controls plugged in, as in `jarvis chat`
    assert await tool_result(brain) == NO_ORB


async def test_a_new_centrepiece_is_passed_on():
    switch = ToolCall(name="display_orb", args={"action": "switch", "core": "mjolnir"}, id="c3")
    brain = make_brain(AIMessage("", tool_calls=[switch]), "Mjölnir it is, sir.")
    asked: list[DisplayCommand] = []

    async def desktop(command: DisplayCommand) -> str:
        asked.append(command)
        return "The orb now has Mjölnir in the middle."

    brain.display = desktop
    await say(brain, "Put Thor's hammer in the middle.")
    assert asked == [DisplayCommand("switch", core="mjolnir")]


def test_commands_travel_as_json_and_nonsense_is_refused():
    move = DisplayCommand("move", "bottom left", "other")
    switch = DisplayCommand("switch", core="iron-man-1")
    for command in (move, switch):
        assert DisplayCommand.from_json(command.to_json()) == command
    assert DisplayCommand.from_json('{"action": "hide"}') == DisplayCommand("hide")
    for nonsense in [
        '{"action": "explode"}',
        '{"action": "move", "corner": "middle"}',
        '{"action": "switch", "core": "shield"}',
    ]:
        with pytest.raises(ValueError):
            DisplayCommand.from_json(nonsense)
