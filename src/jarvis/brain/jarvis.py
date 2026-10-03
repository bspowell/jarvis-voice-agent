"""Jarvis, the orchestrator agent: the personality you talk to."""

from langchain.agents import create_agent
from langchain.agents.middleware import ToolErrorMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from jarvis.brain.models import make_model
from jarvis.config import Settings
from jarvis.prompts import load_prompt
from jarvis.tools.errors import explain_tool_error
from jarvis.tools.weather import get_weather


def build_jarvis(
    settings: Settings, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    """Assemble Jarvis: model + personality + tools + memory."""
    return create_agent(
        model=make_model(settings.models.jarvis),
        tools=[get_weather],
        system_prompt=load_prompt("jarvis"),
        # Middleware wraps each step of the loop; this one catches tool failures.
        middleware=[ToolErrorMiddleware(explain_tool_error)],
        checkpointer=checkpointer,
        name="jarvis",
    )
