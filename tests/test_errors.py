"""What the model hears when a tool breaks: service trouble becomes a note; bugs still crash."""

import httpx2
from langchain.agents.middleware import ToolCallRequest

from jarvis.auth.store import NotSignedIn
from jarvis.tools.errors import explain_tool_error

REQUEST = ToolCallRequest(
    tool_call={"name": "notion-search", "args": {}, "id": "n1"}, tool=None, state={}, runtime=None
)


def wrapped(cause: Exception) -> RuntimeError:
    """How FastMCP reports trouble: its own error, caused by the real one."""
    try:
        raise RuntimeError("Client failed to connect") from cause
    except RuntimeError as error:
        return error


def test_a_wrapped_network_error_becomes_a_note():
    error = wrapped(httpx2.ConnectError("Connection refused"))
    assert explain_tool_error(error, REQUEST) == (
        "notion-search failed: the online service didn't answer (ConnectError)."
    )


def test_a_wrapped_sign_in_problem_becomes_a_note():
    error = wrapped(NotSignedIn("Not connected to Notion."))
    assert explain_tool_error(error, REQUEST) == (
        "notion-search failed: NotSignedIn: Not connected to Notion."
    )


def test_a_bug_still_crashes():
    assert explain_tool_error(wrapped(KeyError("title")), REQUEST) is None
    assert explain_tool_error(ValueError("bad"), REQUEST) is None
