"""What the model hears when a tool breaks, so Jarvis can apologise instead of crashing."""

from collections.abc import Iterator

import anthropic
import httpx
import httpx2
from firecrawl.v2.utils.error_handler import FirecrawlError
from google.auth.exceptions import TransportError
from langchain.agents.middleware import ToolCallRequest
from mcp.shared.exceptions import MCPError

from jarvis.auth.store import NotSignedIn
from jarvis.tools.google_tasks import TasksSwitchedOff

# An online service answered, but with trouble: worth an apology, not a crash.
SERVICE_ERRORS = (
    FirecrawlError,  # the search service: out of credits, rate-limited, page blocked…
    NotSignedIn,  # Google or Notion access removed mid-request
    TasksSwitchedOff,  # Google Tasks not turned on for Jarvis's Google app
    TransportError,  # Google's sign-in service unreachable while renewing access
    MCPError,  # an MCP server (Notion) refused a request
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
    for problem in causes(error):
        # httpx2: the HTTP library under the MCP client (FastMCP); same errors as httpx.
        if isinstance(problem, httpx.HTTPStatusError | httpx2.HTTPStatusError):
            status = problem.response.status_code  # it answered, with "no" (403, 429, 500…)
            return f"{tool_name} failed: the online service refused ({status})."
        if isinstance(problem, httpx.HTTPError | httpx2.HTTPError):
            kind = type(problem).__name__  # e.g. ConnectError, ReadTimeout
            return f"{tool_name} failed: the online service didn't answer ({kind})."
        if isinstance(problem, SERVICE_ERRORS):
            return f"{tool_name} failed: {type(problem).__name__}: {problem}"
    return None


def causes(error: BaseException) -> Iterator[BaseException]:
    """The error, then what caused it: libraries often wrap the real problem
    (FastMCP: "Client failed to connect", caused by the network error)."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__
