"""What the model hears when a tool breaks, so Jarvis can apologise instead of crashing."""

import httpx
from langchain.agents.middleware import ToolCallRequest


def explain_tool_error(error: Exception, request: ToolCallRequest) -> str | None:
    """Network trouble becomes a note for the model; anything else is a bug and stops the run."""
    if isinstance(error, httpx.HTTPError):
        tool_name = request.tool_call["name"]
        return f"{tool_name} failed: the online service didn't answer ({type(error).__name__})."
    return None
