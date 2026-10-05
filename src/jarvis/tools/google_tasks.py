"""Google Tasks tools for the Calendar agent: the user's to-dos. Read them; add and change them.

Tasks are Google's to-do lists (they show beside events in Google Calendar). Same sign-in as the
calendar and the same rule: adding or changing a task waits for your OK (`agents/calendar.py`),
`describe_new_task` / `describe_task_change` say exactly what you're approving, and there's no
delete tool. Google keeps only the day a task is due, never a time of day.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from langchain.agents.middleware import AgentState, ToolCallRequest
from langchain.tools import tool
from langchain_core.messages import AnyMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime

from jarvis.auth.google import NotSignedIn, access_token
from jarvis.config import get_settings
from jarvis.tools.google_calendar import TIMEOUT_SECONDS, day

API = "https://tasks.googleapis.com/tasks/v1"
MAX_TASKS = 50  # enough to read out; keeps the agent's reading short
MAX_NOTES = 300  # characters of a task's notes the agent sees
NOTHING_TO_CHANGE = (
    "Nothing to change: pass a new title, due day or notes, or done to tick it off (or reopen it)."
)
TASKS_HAVE_NO_TIME = (
    "Tasks can't hold a time of day: Google keeps only the day. If the user gave a time, save "
    "nothing yet: tell them, and ask whether they'd like a task due that day without the time, "
    "or a calendar event at that time. Otherwise pass due as a day, e.g. 2026-10-09."
)


class TasksSwitchedOff(RuntimeError):
    """Google Tasks is switched off for Jarvis's Google app (its Google Cloud project)."""


# --- Tools (what the Calendar agent's model sees) ---
@tool(parse_docstring=True)
async def list_tasks(due_by: str | None = None, include_done: bool = False) -> dict[str, Any]:
    """List the user's tasks (to-dos) from all their task lists, soonest due first.

    Args:
        due_by: Only tasks due on or before this day, e.g. "2026-10-09". Leave out for all.
        include_done: Also list tasks already ticked off.
    """
    async with tasks_client() as tasks:
        return await find_tasks(tasks, due_by, include_done)


@tool(parse_docstring=True)
async def add_task(
    title: str, due: str | None = None, notes: str | None = None, task_list: str | None = None
) -> dict[str, Any]:
    """Add a task (a to-do). The user is asked to confirm before anything is saved.

    Args:
        title: What to do, e.g. "Buy candles".
        due: The day it's due, e.g. "2026-10-09". Tasks can't hold a time of day.
        notes: Details, if the user gave any.
        task_list: The list's name, as list_tasks shows it. Leave out for the main one.
    """
    async with tasks_client() as tasks:
        return await add_new_task(tasks, title, due, notes, task_list)


@tool(parse_docstring=True)
async def update_task(
    task_id: str,
    task_list: str | None = None,
    title: str | None = None,
    due: str | None = None,
    notes: str | None = None,
    done: bool | None = None,
) -> dict[str, Any]:
    """Change a task or tick it off: only the fields you pass change. The user confirms first.

    Args:
        task_id: The task's id, from list_tasks.
        task_list: Its list's name, as list_tasks shows it. Leave out for the main one.
        title: New wording.
        due: New due day, e.g. "2026-10-09".
        notes: New notes.
        done: true to tick it off, false to reopen it.
    """
    async with tasks_client() as tasks:
        return await change_task(tasks, task_id, task_list, title, due, notes, done)


# --- The Tasks API ---
class GoogleTasks:
    """The few Tasks API calls Jarvis needs, signed with your access token."""

    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self.http = http
        self.headers = {"Authorization": f"Bearer {token}"}

    async def call(self, method: str, path: str, **options: Any) -> dict[str, Any]:
        response = await self.http.request(method, API + path, headers=self.headers, **options)
        if response.status_code == 401:
            raise NotSignedIn("Google didn't accept Jarvis's access token: it may have expired.")
        if response.status_code == 403 and "SERVICE_DISABLED" in response.text:
            raise TasksSwitchedOff(
                "Google Tasks is switched off for Jarvis's Google app: turn on the Tasks API in "
                "its Google Cloud project, then ask again."
            )
        response.raise_for_status()
        return response.json() if response.content else {}

    async def lists(self) -> list[dict[str, Any]]:
        """Your task lists. The first is the main one ("My Tasks")."""
        found = await self.call("GET", "/users/@me/lists", params={"maxResults": 100})
        return found.get("items", [])

    async def tasks(self, list_id: str, include_done: bool) -> list[dict[str, Any]]:
        params = {"maxResults": 100, "showCompleted": str(include_done).lower()}
        if include_done:
            params["showHidden"] = "true"  # ticked off in Google's apps (they "hide" done tasks)
        found = await self.call("GET", f"/lists/{quote(list_id, safe='')}/tasks", params=params)
        return found.get("items", [])

    async def task(self, list_id: str, task_id: str) -> dict[str, Any] | None:
        """One task, or None if there's no such task."""
        path = f"/lists/{quote(list_id, safe='')}/tasks/{quote(task_id, safe='')}"
        try:
            return await self.call("GET", path)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (400, 404):  # unknown task id
                return None
            raise

    async def create(self, list_id: str, task: dict[str, Any]) -> dict[str, Any]:
        return await self.call("POST", f"/lists/{quote(list_id, safe='')}/tasks", json=task)

    async def update(self, list_id: str, task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """PATCH: Google changes only the fields sent."""
        path = f"/lists/{quote(list_id, safe='')}/tasks/{quote(task_id, safe='')}"
        return await self.call("PATCH", path, json=changes)


@asynccontextmanager
async def tasks_client() -> AsyncIterator[GoogleTasks]:
    """A Tasks API client for one tool call, signed in as you."""
    token = await access_token(get_settings())
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
        yield GoogleTasks(http, token)


# --- Reading ---
async def find_tasks(tasks: GoogleTasks, due_by: str | None, include_done: bool) -> dict[str, Any]:
    """Compact facts: each task's id, title, list, due day in words, and whether it's done."""
    try:
        last_day = due_day(due_by) if due_by else None
    except ValueError as error:
        return {"error": str(error)}
    lists = await tasks.lists()
    found = await asyncio.gather(*(tasks.tasks(each["id"], include_done) for each in lists))
    rows = []
    for task_list, items in zip(lists, found, strict=True):
        for task in items:
            due = due_on(task)
            if task.get("deleted") or not task.get("title", "").strip():
                continue  # deleted, or an empty line someone left in the list
            if last_day and (due is None or due > last_day):
                continue
            rows.append((due or date.max, compact_task(task, task_list)))
    rows.sort(key=lambda row: row[0])  # undated tasks last
    result: dict[str, Any] = {
        "tasks": [task for _, task in rows[:MAX_TASKS]],
        "task_lists": [each.get("title", each["id"]) for each in lists],
    }
    if len(rows) > MAX_TASKS:
        result["more_not_shown"] = len(rows) - MAX_TASKS
    return result


def compact_task(task: dict[str, Any], task_list: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {
        "id": task["id"],
        "title": task.get("title", "(no title)"),
        "list": task_list.get("title", task_list["id"]),
    }
    if due := due_on(task):
        found["due"] = day(due)
    if task.get("status") == "completed":
        found["done"] = True
    if notes := task.get("notes"):
        found["notes"] = notes[:MAX_NOTES]
    return found


def due_on(task: dict[str, Any]) -> date | None:
    """The day a task is due. Google stores it as midnight UTC: the date part is the day."""
    return date.fromisoformat(task["due"][:10]) if task.get("due") else None


def due_day(text: str) -> date:
    """ "2026-10-09" → that day (a time, if the model adds one, is ignored). Raises ValueError."""
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        raise ValueError(f"Couldn't read {text!r}: give the due day as 2026-10-09.") from None


def find_list(lists: Sequence[dict[str, Any]], name: str | None) -> dict[str, Any] | None:
    """A task list by name; no name (or "main") means the first, your main list."""
    if not lists:
        return None
    wanted = (name or "main").strip().lower()
    if wanted in ("main", "my tasks"):
        return lists[0]
    return next((each for each in lists if each.get("title", "").lower() == wanted), None)


def no_such_list(lists: Sequence[dict[str, Any]], name: str | None) -> dict[str, str]:
    names = ", ".join(each.get("title", each["id"]) for each in lists) or "none"
    return {"error": f"No task list called {name!r}. Your task lists: {names}."}


# --- Writing (runs only after the user approves) ---
async def add_new_task(
    tasks: GoogleTasks, title: str, due: str | None, notes: str | None, list_name: str | None
) -> dict[str, Any]:
    if has_time(due):
        return {"error": TASKS_HAVE_NO_TIME}
    lists = await tasks.lists()
    target = find_list(lists, list_name)
    if target is None:
        return no_such_list(lists, list_name)
    task: dict[str, Any] = {"title": title}
    try:
        if due:
            task["due"] = f"{due_day(due).isoformat()}T00:00:00.000Z"
    except ValueError as error:
        return {"error": str(error)}
    if notes:
        task["notes"] = notes
    created = await tasks.create(target["id"], task)
    return {"added_task": compact_task(created, target)}


async def change_task(
    tasks: GoogleTasks,
    task_id: str,
    list_name: str | None,
    title: str | None,
    due: str | None,
    notes: str | None,
    done: bool | None,
) -> dict[str, Any]:
    if has_time(due):
        return {"error": TASKS_HAVE_NO_TIME}
    lists = await tasks.lists()
    target = find_list(lists, list_name)
    if target is None:
        return no_such_list(lists, list_name)
    if await tasks.task(target["id"], task_id) is None:
        return {"error": f"No task with id {task_id!r} on the {target.get('title')!r} list."}
    changes: dict[str, Any] = {}
    if title:
        changes["title"] = title
    try:
        if due:
            changes["due"] = f"{due_day(due).isoformat()}T00:00:00.000Z"
    except ValueError as error:
        return {"error": str(error)}
    if notes:
        changes["notes"] = notes
    if done is True:
        changes["status"] = "completed"
    elif done is False:
        changes |= {"status": "needsAction", "completed": None}  # reopened: no completion date
    if not changes:
        return {"error": NOTHING_TO_CHANGE}
    updated = await tasks.update(target["id"], task_id, changes)
    return {"changed_task": compact_task(updated, target)}


# --- What you're approving (the confirmation step reads these; see agents/calendar.py) ---
def describe_new_task(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    """add_task, in exact words: built by code from the arguments, so it can't drift."""
    args = tool_call["args"]
    where = list_words(args.get("task_list"))
    text = f"Add the task “{args.get('title', '(no title)')}” to {where}"
    if args.get("due"):
        text += f", due {due_words(args['due'])}"
    return text + "."


def describe_task_change(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str:
    """update_task, in exact words. Names the task as list_tasks showed it in this request."""
    args = tool_call["args"]
    listed = listed_task(state["messages"], args.get("task_id", ""))
    target = f"the task “{listed['title']}”" if listed else "a task"
    return f"Change {target}: {'; '.join(requested_task_changes(args))}."


def ask_about_new_task(request: ToolCallRequest) -> bool:
    """Ask only about a task that can be saved: one with a time of day is refused (Google can't
    keep it), so you're never asked to approve a task whose time would quietly vanish.
    (Safe as a `when` check: the arguments can't change between asking and running.)"""
    return not has_time(request.tool_call["args"].get("due"))


def ask_about_task_change(request: ToolCallRequest) -> bool:
    """Ask only about a change that changes something and can be saved (the others are refused).
    (Safe as a `when` check: the arguments can't change between asking and running.)"""
    args = request.tool_call["args"]
    return bool(requested_task_changes(args)) and not has_time(args.get("due"))


def has_time(due: str | None) -> bool:
    """Does a due value carry a time of day ("2026-10-06T07:30", "7:30")? Tasks can't hold one."""
    return bool(due) and re.search(r"T\d|\d:\d\d", due) is not None


def requested_task_changes(args: dict[str, Any]) -> list[str]:
    """What update_task's arguments change, in words; empty if nothing."""
    changes = []
    if args.get("done") is True:
        changes.append("tick it off as done")
    elif args.get("done") is False:
        changes.append("reopen it (not done)")
    if args.get("title"):
        changes.append(f"reword it “{args['title']}”")
    if args.get("due"):
        changes.append(f"make it due {due_words(args['due'])}")
    if args.get("notes"):
        changes.append("replace its notes")
    return changes


def list_words(name: str | None) -> str:
    """ "your Shopping list"; "your tasks" for the main list."""
    if not name:
        return "your tasks"
    return f"your {name}" if name.lower().endswith("list") else f"your {name} list"


def due_words(text: str) -> str:
    try:
        return day(due_day(text))
    except ValueError:
        return text


def listed_task(messages: Sequence[AnyMessage], task_id: str) -> dict[str, Any] | None:
    """The task as list_tasks showed it earlier in this request, or None."""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == list_tasks.name:
            try:
                found = json.loads(message.text).get("tasks", [])
            except ValueError:
                continue
            for task in found:
                if task.get("id") == task_id:
                    return task
    return None
