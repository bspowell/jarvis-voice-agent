"""What the model hears when a tool breaks, so Jarvis can apologise instead of crashing."""

import anthropic
import httpx
from firecrawl.v2.utils.error_handler import FirecrawlError
from langchain.agents.middleware import ToolCallRequest

# An online service answered, but with trouble: worth an apology, not a crash.
SERVICE_ERRORS = (
    FirecrawlError,  # the search service: out of credits, rate-limited, page blocked…
    # A sub-agent's model, after its own retries: busy, down or unreachable.
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
)


def explain_tool_error(error: Exception, request: ToolCallRequest) -> str | None:
    """Service trouble becomes a note for the model; anything else is a bug and stops the run."""
    tool_name = request.tool_call["name"]
    if isinstance(error, httpx.HTTPError):
        return f"{tool_name} failed: the online service didn't answer ({type(error).__name__})."
    if isinstance(error, SERVICE_ERRORS):
        return f"{tool_name} failed: {type(error).__name__}: {error}"
    return None
