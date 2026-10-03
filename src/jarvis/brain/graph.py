"""Jarvis for LangGraph Studio: `uv run langgraph dev` finds this via `langgraph.json`.

Studio runs the graph on its own local server, which supplies memory (a checkpointer)
itself, so Jarvis is built without one. Studio skips `Brain`, so messages carry no timestamp.
"""

from langgraph.graph.state import CompiledStateGraph

from jarvis.brain.jarvis import build_jarvis
from jarvis.config import get_settings


def make_graph() -> CompiledStateGraph:
    return build_jarvis(get_settings())
