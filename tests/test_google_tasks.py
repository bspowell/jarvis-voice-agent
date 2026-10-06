"""Google Tasks tools: what the Calendar agent reads, and exactly what it writes after your OK."""

import json

import httpx
import pytest
from fakes import CANDLES, MY_TASKS, FakeGoogleTasks
from langchain_core.messages import ToolCall, ToolMessage

from jarvis.auth.google import NotSignedIn
from jarvis.tools.google_tasks import (
    NOTHING_TO_CHANGE,
    TASKS_HAVE_NO_TIME,
    GoogleTasks,
    TasksSwitchedOff,
    add_new_task,
    change_task,
    describe_new_task,
    describe_task_change,
    find_tasks,
    requested_task_changes,
)


async def test_open_tasks_from_every_list_soonest_due_first():
    found, todo = await find_tasks(FakeGoogleTasks(), None, False)
    assert found == {
        "tasks": [
            {"id": "task-milk", "title": "Milk", "list": "Shopping", "due": "Tue 6 Oct 2026"},
            {
                "id": "task-candles",
                "title": "Buy candles",
                "list": "My Tasks",
                "due": "Fri 9 Oct 2026",
            },
            {  # no due day: last
                "id": "task-plumber",
                "title": "Call the plumber",
                "list": "My Tasks",
                "notes": "Leak under the sink",
            },
        ],  # done ones and empty lines aren't listed
        "task_lists": ["My Tasks", "Shopping"],
    }
    assert todo == {  # for the to-dos card: due days as dates
        "due_by": None,
        "tasks": [
            {"title": "Milk", "due": "2026-10-06", "done": False, "list": "Shopping"},
            {"title": "Buy candles", "due": "2026-10-09", "done": False, "list": "My Tasks"},
            {"title": "Call the plumber", "due": None, "done": False, "list": "My Tasks"},
        ],
    }


async def test_due_by_keeps_only_tasks_due_by_that_day():
    found, todo = await find_tasks(FakeGoogleTasks(), "2026-10-07", False)
    assert [task["title"] for task in found["tasks"]] == ["Milk"]
    assert todo["due_by"] == "2026-10-07"


async def test_ticked_off_tasks_only_when_asked_for():
    found, _todo = await find_tasks(FakeGoogleTasks(), None, True)
    taxes = next(task for task in found["tasks"] if task["title"] == "File taxes")
    assert taxes["done"] is True


async def test_a_new_task_goes_to_the_main_list_due_on_its_day():
    tasks = FakeGoogleTasks()
    result = await add_new_task(tasks, "Book the vet", "2026-10-10", None, None)
    assert tasks.created == [
        (MY_TASKS["id"], {"title": "Book the vet", "due": "2026-10-10T00:00:00.000Z"})
    ]
    assert result["added_task"]["due"] == "Sat 10 Oct 2026"


@pytest.mark.parametrize("due", ["2026-10-06T07:30", "2026-10-06 7:30"])
async def test_a_task_with_a_time_is_refused_so_the_user_can_choose(due):
    # Google would quietly drop the time: never save it, so the user hears the choice instead
    tasks = FakeGoogleTasks()
    assert await add_new_task(tasks, "Start studying", due, None, None) == {
        "error": TASKS_HAVE_NO_TIME
    }
    assert await change_task(tasks, "task-candles", None, None, due, None, None) == {
        "error": TASKS_HAVE_NO_TIME
    }
    assert tasks.created == tasks.updated == []


async def test_a_list_name_that_matches_nothing_names_the_real_ones():
    tasks = FakeGoogleTasks()
    result = await add_new_task(tasks, "Eggs", None, None, "Groceries")
    assert result == {
        "error": "No task list called 'Groceries'. Your task lists: My Tasks, Shopping."
    }
    assert tasks.created == []


@pytest.mark.parametrize(
    ("done", "sent"),
    [
        (True, {"status": "completed"}),
        (False, {"status": "needsAction", "completed": None}),  # reopened: no completion date
    ],
)
async def test_a_task_is_ticked_off_or_reopened(done, sent):
    tasks = FakeGoogleTasks()
    result = await change_task(tasks, "task-candles", None, None, None, None, done)
    assert tasks.updated == [(MY_TASKS["id"], "task-candles", sent)]
    assert result["changed_task"]["title"] == "Buy candles"


async def test_a_task_change_that_changes_nothing_is_refused():
    tasks = FakeGoogleTasks()
    assert await change_task(tasks, "task-candles", None, None, None, None, None) == {
        "error": NOTHING_TO_CHANGE
    }
    assert tasks.updated == []


async def test_changing_an_unknown_task_is_a_fact():
    tasks = FakeGoogleTasks()
    result = await change_task(tasks, "task-nope", None, "New", None, None, None)
    assert "No task with id 'task-nope'" in result["error"]


def test_the_confirmations_say_exactly_what_happens():
    call = ToolCall(name="add_task", args={"title": "Book the vet", "due": "2026-10-10"}, id="a1")
    assert describe_new_task(call, {"messages": []}, None) == (
        "Add the task “Book the vet” to your tasks, due Sat 10 Oct 2026."
    )
    listing = ToolMessage(
        json.dumps({"tasks": [{"id": CANDLES["id"], "title": "Buy candles"}]}),
        tool_call_id="l1",
        name="list_tasks",
    )
    tick = ToolCall(name="update_task", args={"task_id": CANDLES["id"], "done": True}, id="u1")
    assert describe_task_change(tick, {"messages": [listing]}, None) == (
        "Change the task “Buy candles”: tick it off as done."
    )


def test_only_real_task_changes_are_put_to_you():
    assert requested_task_changes({"task_id": "task-candles", "task_list": "My Tasks"}) == []
    assert requested_task_changes({"task_id": "task-candles", "done": False}) == [
        "reopen it (not done)"
    ]


async def test_calls_reach_the_tasks_api_signed_in_and_safely_encoded():
    seen: list[httpx.Request] = []

    def google(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [], "id": "t1", "title": "x"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(google))
    tasks = GoogleTasks(http, token="access-1")
    await tasks.lists()
    await tasks.tasks("list/1", include_done=False)
    await tasks.update("list/1", "t1", {"status": "completed"})

    lists, listing, change = seen
    assert lists.headers["Authorization"] == "Bearer access-1"
    assert lists.url.path == "/tasks/v1/users/@me/lists"
    assert listing.url.raw_path.startswith(b"/tasks/v1/lists/list%2F1/tasks")
    assert listing.url.params["showCompleted"] == "false"
    assert (change.method, json.loads(change.content)) == ("PATCH", {"status": "completed"})


async def test_tasks_switched_off_for_jarvis_says_so():
    disabled = {"error": {"code": 403, "details": [{"reason": "SERVICE_DISABLED"}]}}
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(403, json=disabled))
    )
    with pytest.raises(TasksSwitchedOff, match="turn on the Tasks API"):
        await GoogleTasks(http, token="access-1").lists()


async def test_a_refused_token_means_not_signed_in():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(NotSignedIn):
        await GoogleTasks(http, token="access-old").lists()
