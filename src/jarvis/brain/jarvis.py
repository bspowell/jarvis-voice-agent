"""Jarvis, the orchestrator agent: the personality you talk to."""

from langchain.agents import create_agent
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from jarvis.brain.models import make_model
from jarvis.config import Settings
from jarvis.prompts import load_prompt


def build_jarvis(
    settings: Settings, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    """Assemble Jarvis: model + personality + tools (none yet) + memory."""
    return create_agent(
        model=make_model(settings.models.jarvis),
        tools=[],
        system_prompt=load_prompt("jarvis"),
        checkpointer=checkpointer,
        name="jarvis",
    )
