"""The Calendar agent: Jarvis's diary keeper (Google Calendar and Google Tasks), a sub-agent
wrapped as one tool.

Like Search, it has its own model, prompt and tools, and Jarvis hears only its short report.
Unlike Search, it can change things, so every write waits for your OK:
`HumanInTheLoopMiddleware` pauses the run just before a write tool (an event or a task) would
run (a LangGraph interrupt). The pause travels up through Jarvis's `calendar_agent` tool to
`Brain`, which asks you "Shall I…?" and resumes the run with your answer.

Pausing needs saved state, so this agent keeps the default checkpointer setting (`None`: each
request is saved in Jarvis's memory, under the tool call, until it finishes). Search uses
`False` because it never pauses.
"""

import json
from datetime import datetime

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain.tools import BaseTool, ToolRuntime, tool
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from jarvis.agents.writes import needs_approval, with_receipts
from jarvis.auth.google import NOT_SET_UP, NotSignedIn, access_token, is_set_up
from jarvis.brain.brain import timestamped
from jarvis.brain.context import JarvisContext
from jarvis.brain.models import make_model
from jarvis.config import Settings
from jarvis.events import Event
from jarvis.prompts import load_prompt
from jarvis.tools.errors import explain_tool_error
from jarvis.tools.google_calendar import (
    TIMEOUT_SECONDS,
    GoogleCalendar,
    ask_about_change,
    create_event,
    describe_event_change,
    describe_new_event,
    list_events,
    shown_calendars,
    update_event,
)
from jarvis.tools.google_tasks import (
    GoogleTasks,
    TasksSwitchedOff,
    add_task,
    ask_about_new_task,
    ask_about_task_change,
    describe_new_task,
    describe_task_change,
    list_tasks,
    update_task,
)

MAX_TOOL_CALLS = 8  # a busy request: list, propose, propose again after "make it 1 PM"…
SIGN_IN_FIRST = (
    "Not signed in to Google, so the calendar is out of reach. A “Sign in with Google” button "
    "is showing for the user now. Once they say they've signed in, ask me again."
)


# No delete tools, on purpose.
TOOLS = [list_events, create_event, update_event, list_tasks, add_task, update_task]
READ_TOOLS = {list_events.name, list_tasks.name}  # every other tool changes things: needs your OK
# Each tool that changes your calendar or tasks, with the words that say what it's about to do.
WRITE_TOOLS = {
    create_event.name: needs_approval(describe_new_event),
    update_event.name: needs_approval(describe_event_change, when=ask_about_change),
    add_task.name: needs_approval(describe_new_task, when=ask_about_new_task),
    update_task.name: needs_approval(describe_task_change, when=ask_about_task_change),
}


def build_calendar_agent(model: str) -> CompiledStateGraph:
    return create_agent(
        model=make_model(model),
        tools=TOOLS,
        system_prompt=load_prompt("calendar_agent"),
        middleware=[
            # Nothing is written until you say yes: the run pauses before every write.
            HumanInTheLoopMiddleware(interrupt_on=WRITE_TOOLS),
            ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS),
            ToolErrorMiddleware(explain_tool_error),  # service trouble becomes a note
        ],
        # No `checkpointer=`: the default lets a paused write resume (see the top of this file).
        name="calendar_agent",
    )


def make_calendar_tool(settings: Settings) -> BaseTool:
    """Wrap the Calendar agent as the `calendar_agent` tool that Jarvis calls."""
    agent = build_calendar_agent(settings.models.calendar)

    @tool(parse_docstring=True)
    async def calendar_agent(request: str, runtime: ToolRuntime[JarvisContext]) -> str:
        """Ask your Calendar agent about the user's Google Calendar and to-dos (Google Tasks):
        what's on, when they're free, what's due, or adding and changing events and tasks.
        It reports back briefly.

        Args:
            request: The user's request in full, with names, days and times as they said them,
                e.g. "Book lunch with Sam tomorrow at noon".
        """
        if not is_set_up(settings):
            return NOT_SET_UP
        try:
            await access_token(settings)  # proves the sign-in still works (renewing it if needed)
        except NotSignedIn:
            runtime.stream_writer(Event("sign_in_needed", {"service": "google"}))  # the button
            return SIGN_IN_FIRST
        runtime.stream_writer(Event("agent_started", {"agent": "calendar"}))  # cue the filler line
        task = timestamped(request, datetime.now().astimezone())
        # After you answer "Shall I…?", this tool runs again from the top. LangGraph then ignores
        # the new task and resumes the paused run from its saved state.
        result = await agent.ainvoke({"messages": [HumanMessage(task)]})
        # The report, plus facts Jarvis can trust: what you changed, what Google saved.
        return with_receipts(result["messages"], receipt_in, "Saved in the calendar")

    return calendar_agent


def receipt_in(message: AnyMessage) -> str | None:
    """ "added “Lunch with Sam”, Mon 5 Oct 2026, 1 PM to 2 PM", from a write's result."""
    if not isinstance(message, ToolMessage) or message.name not in WRITE_TOOLS:
        return None
    try:
        result = json.loads(message.text)
    except ValueError:
        return None
    for key, done in (("created", "added"), ("updated", "changed")):
        if event := result.get(key):
            where = "" if event["calendar"] == "main" else f" ({event['calendar']} calendar)"
            return f"{done} “{event['title']}”, {event['when']}{where}"
    for key, done in (("added_task", "added the task"), ("changed_task", "changed the task")):
        if task := result.get(key):
            due = f", due {task['due']}" if task.get("due") else ""
            return f"{done} “{task['title']}”{due}{', done' if task.get('done') else ''}"
    return None


async def check_calendar(settings: Settings) -> str:
    """Health check: a status line if the Calendar agent can work; raises with the reason if not."""
    if not is_set_up(settings):
        raise RuntimeError(NOT_SET_UP)
    try:
        token = await access_token(settings)
    except NotSignedIn as error:
        raise RuntimeError(f"{error} Ask about your calendar to sign in.") from None
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
        calendars = await GoogleCalendar(http, token).calendars()
        try:
            lists = len(await GoogleTasks(http, token).lists())
            tasks = f"Tasks ready, {lists} list{'' if lists == 1 else 's'}"
        except TasksSwitchedOff:
            tasks = "Tasks switched off (turn on the Tasks API in Google Cloud)"
    return f"Google Calendar ready, {len(shown_calendars(calendars))} calendars; {tasks}."
