"""Stand-ins for the model, the web, Google, Notion, the Keychain and the voice services: tests
run free and offline."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import httpx
from fastmcp import Client, FastMCP
from firecrawl.v2.types import (
    CreditUsage,
    Document,
    DocumentMetadata,
    SearchData,
    SearchResultWeb,
)
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableLambda
from livekit import rtc
from livekit.agents import stt
from livekit.agents.voice import io
from pydantic import Field


class FakeToolCallingModel(GenericFakeChatModel):
    """Scripted replies that may "call" tools: lets tests run the whole agent loop.

    LangChain's `GenericFakeChatModel` can't accept tools or stream tool calls; this adds both.
    `seen` records the messages each call was sent.
    """

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolCallingModel":
        return self  # the script already says which tools to call

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.seen.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        reply = self._generate(messages).generations[0].message
        for piece in re.split(r"(\s)", reply.text):  # word by word, like a real stream
            if piece:
                yield ChatGenerationChunk(message=AIMessageChunk(content=piece, id=reply.id))
        if reply.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", id=reply.id, tool_calls=reply.tool_calls)
            )


# --- The web ---
GEOCODER = "geocoding-api.open-meteo.com"
FORECASTER = "api.open-meteo.com"
IP_LOOKUP = "ipinfo.io"


def fake_web(answers: dict[str, Any], seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    """An HTTP client that never leaves the machine: each host replies with canned JSON.

    A reply may also be an `httpx.Response` (e.g. a 500) or an exception to raise.
    Requests are appended to `seen`, so tests can check what was asked.
    """

    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        reply = answers[request.url.host]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


# Real response shapes, trimmed to the fields Jarvis reads.
PARIS_RESULTS = {
    "results": [
        {
            "name": "Paris",
            "admin1": "Île-de-France",
            "country": "France",
            "latitude": 48.85,
            "longitude": 2.35,
        }
    ]
}
LOCAL_GEO_RESULTS = {
    "results": [
        {
            "name": "Vancouver",
            "admin1": "British Columbia",
            "country": "Canada",
            "latitude": 49.25,
            "longitude": -123.12,
        }
    ]
}
NO_RESULTS = {"generationtime_ms": 0.4}  # Open-Meteo's answer when nothing matches
VANCOUVER_IP = {
    "city": "Vancouver",
    "region": "British Columbia",
    "country": "CA",
    "loc": "49.2497,-123.1193",
}
IP_UNKNOWN = {"ip": "127.0.0.1", "bogon": True}  # private addresses have no location
FORECAST = {
    "current": {
        "temperature_2m": 16.9,
        "apparent_temperature": 17.4,
        "weather_code": 3,
        "wind_speed_10m": 6.2,
    },
    "daily": {
        "weather_code": [61],
        "temperature_2m_max": [17.6],
        "temperature_2m_min": [12.2],
        "precipitation_probability_max": [80],
        "precipitation_sum": [4.2],
    },
}


# --- Firecrawl (web search) ---
class FakeFirecrawl:
    """Stands in for `AsyncFirecrawl`: canned results and pages, plus a record of what was asked.

    A page may also be an exception to raise, e.g. a site that blocks scraping.
    """

    def __init__(
        self,
        results: list[SearchResultWeb] | None = None,
        pages: dict[str, str | Exception] | None = None,
        credits: int = 500,
    ) -> None:
        self.results = results or []
        self.pages = pages or {}
        self.credits = credits
        self.searches: list[str] = []
        self.reads: list[str] = []

    async def search(self, query: str, **options: Any) -> SearchData:
        self.searches.append(query)
        return SearchData(web=self.results[: options.get("limit", 5)])

    async def scrape(self, url: str, **options: Any) -> Document:
        self.reads.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return Document(markdown=page, metadata=DocumentMetadata(title=f"Page at {url}"))

    async def get_credit_usage(self) -> CreditUsage:
        return CreditUsage(remaining_credits=self.credits)


RELEASES = "https://github.com/langchain-ai/langgraph/releases"
PYPI = "https://pypi.org/project/langgraph/"
BLOG = "https://blog.langchain.com/"
LANGGRAPH_RESULTS = [
    SearchResultWeb(url=RELEASES, title="Releases · langgraph", description="langgraph 1.2.12"),
    SearchResultWeb(url=PYPI, title="langgraph · PyPI", description="Released: Sep 30, 2026"),
    SearchResultWeb(url=BLOG, title="LangChain Blog", description="What's new in LangGraph"),
]
LANGGRAPH_PAGES = {
    RELEASES: "# langgraph 1.2.12\n\nFixes a checkpoint bug.",
    PYPI: "# langgraph 1.2.12\n\nReleased: Sep 30, 2026",
    BLOG: "# What's new\n\nDurable execution, faster streaming.",
}


# --- The Keychain ---
class MemoryKeychain(KeyringBackend):
    """Stands in for the macOS Keychain: entries live in a dictionary."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.entries.pop((service, username), None) is None:
            raise PasswordDeleteError("No such entry.")


# --- Google Calendar ---
ZONE = "America/Vancouver"
MAIN_CALENDAR = {
    "id": "sean@example.com",
    "summary": "sean@example.com",
    "primary": True,
    "selected": True,
    "accessRole": "owner",
    "timeZone": ZONE,
}
WORK = {
    "id": "work123@group.calendar.google.com",
    "summary": "Work",
    "selected": True,
    "accessRole": "writer",
    "timeZone": ZONE,
}
HOLIDAYS = {
    "id": "en.canadian#holiday@group.v.calendar.google.com",
    "summary": "Holidays in Canada",
    "selected": True,
    "accessRole": "reader",
    "timeZone": ZONE,
}
UNTICKED = {"id": "old@group.calendar.google.com", "summary": "Old project", "accessRole": "owner"}
VACATION = {
    "id": "vacation456@group.calendar.google.com",
    "summary": "Jamie + Alex Vacation TRIPPPP",
    "selected": True,
    "accessRole": "owner",
    "timeZone": ZONE,
}
CALENDARS = [MAIN_CALENDAR, WORK, HOLIDAYS, UNTICKED]

DENTIST = {
    "id": "ev-dentist",
    "summary": "Dentist",
    "location": "Main Dental",
    "start": {"dateTime": "2026-10-05T14:00:00-07:00"},
    "end": {"dateTime": "2026-10-05T15:00:00-07:00"},
}
DECLINED = {
    "id": "ev-declined",
    "summary": "Optional sync",
    "start": {"dateTime": "2026-10-05T11:00:00-07:00"},
    "end": {"dateTime": "2026-10-05T11:30:00-07:00"},
    "attendees": [{"email": "sean@example.com", "self": True, "responseStatus": "declined"}],
}
WORKING_FROM_HOME = {
    "id": "ev-home",
    "eventType": "workingLocation",
    "summary": "Home",
    "start": {"date": "2026-10-05"},
    "end": {"date": "2026-10-06"},
}
STANDUP = {
    "id": "ev-standup",
    "summary": "Standup",
    "start": {"dateTime": "2026-10-05T09:30:00-07:00"},
    "end": {"dateTime": "2026-10-05T09:45:00-07:00"},
}
THANKSGIVING = {
    "id": "ev-thanksgiving",
    "summary": "Thanksgiving",
    "start": {"date": "2026-10-12"},
    "end": {"date": "2026-10-13"},
}


class FakeGoogleCalendar:
    """Stands in for `GoogleCalendar`: calendars and events in memory, plus a record of writes."""

    def __init__(self, calendars: list[dict] | None = None, events: dict | None = None) -> None:
        self.calendar_list = calendars if calendars is not None else CALENDARS
        self.by_calendar: dict[str, list[dict]] = (
            events
            if events is not None
            else {
                MAIN_CALENDAR["id"]: [DENTIST, DECLINED, WORKING_FROM_HOME],
                WORK["id"]: [STANDUP],
                HOLIDAYS["id"]: [THANKSGIVING],
            }
        )
        self.reads: list[tuple[str, str, str, str | None]] = []
        self.created: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []
        self.moved: list[tuple[str, str, str]] = []  # (from calendar, event id, to calendar)

    async def calendars(self) -> list[dict]:
        return self.calendar_list

    async def events(
        self, calendar_id: str, time_min: str, time_max: str, time_zone: str, search: str | None
    ) -> list[dict]:
        self.reads.append((calendar_id, time_min, time_max, search))

        def day(event: dict) -> str:
            return (event["start"].get("dateTime") or event["start"]["date"])[:10]

        return [
            e
            for e in self.by_calendar.get(calendar_id, [])
            if time_min[:10] <= day(e) < time_max[:10]
        ]

    async def event(self, calendar_id: str, event_id: str) -> dict | None:
        return next((e for e in self.by_calendar.get(calendar_id, []) if e["id"] == event_id), None)

    async def create(self, calendar_id: str, event: dict) -> dict:
        self.created.append((calendar_id, event))
        return {"id": f"ev-new-{len(self.created)}", **event}

    async def update(self, calendar_id: str, event_id: str, changes: dict) -> dict:
        self.updated.append((calendar_id, event_id, changes))
        return {**(await self.event(calendar_id, event_id) or {}), **changes}

    async def move(self, calendar_id: str, event_id: str, destination_id: str) -> dict:
        self.moved.append((calendar_id, event_id, destination_id))
        event = await self.event(calendar_id, event_id)
        assert event is not None, "moving an event that isn't there"
        self.by_calendar[calendar_id].remove(event)
        self.by_calendar.setdefault(destination_id, []).append(event)
        return event


# --- Google Tasks ---
MY_TASKS = {"id": "list-main", "title": "My Tasks"}
SHOPPING = {"id": "list-shopping", "title": "Shopping"}
CANDLES = {
    "id": "task-candles",
    "title": "Buy candles",
    "status": "needsAction",
    "due": "2026-10-09T00:00:00.000Z",
}
PLUMBER = {
    "id": "task-plumber",
    "title": "Call the plumber",
    "status": "needsAction",
    "notes": "Leak under the sink",
}
TAXES = {
    "id": "task-taxes",
    "title": "File taxes",
    "status": "completed",
    "due": "2026-04-30T00:00:00.000Z",
    "completed": "2026-04-28T10:00:00.000Z",
}
BLANK = {"id": "task-blank", "title": "", "status": "needsAction"}  # an empty line in the list
MILK = {
    "id": "task-milk",
    "title": "Milk",
    "status": "needsAction",
    "due": "2026-10-06T00:00:00.000Z",
}


class FakeGoogleTasks:
    """Stands in for `GoogleTasks`: task lists in memory, plus a record of writes."""

    def __init__(self) -> None:
        self.task_lists = [MY_TASKS, SHOPPING]
        self.by_list = {MY_TASKS["id"]: [CANDLES, PLUMBER, TAXES, BLANK], SHOPPING["id"]: [MILK]}
        self.created: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []

    async def lists(self) -> list[dict]:
        return self.task_lists

    async def tasks(self, list_id: str, include_done: bool) -> list[dict]:
        found = self.by_list.get(list_id, [])
        return [t for t in found if include_done or t.get("status") != "completed"]

    async def task(self, list_id: str, task_id: str) -> dict | None:
        return next((t for t in self.by_list.get(list_id, []) if t["id"] == task_id), None)

    async def create(self, list_id: str, task: dict) -> dict:
        self.created.append((list_id, task))
        return {"id": f"task-new-{len(self.created)}", "status": "needsAction", **task}

    async def update(self, list_id: str, task_id: str, changes: dict) -> dict:
        self.updated.append((list_id, task_id, changes))
        return {**(await self.task(list_id, task_id) or {}), **changes}


# --- Approvals ---
class FakeApprovalModel(FakeToolCallingModel):
    """Scripted questions (streamed like any reply) and scripted readings of your answers."""

    readings: list[Any] = Field(default_factory=list)  # what each answer "means", in order
    read_requests: list[list[BaseMessage]] = Field(default_factory=list)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> RunnableLambda:
        def read(messages: list[BaseMessage]) -> Any:
            self.read_requests.append(messages)
            return self.readings.pop(0)

        return RunnableLambda(read)


# --- Notion (its MCP server) ---
def notion_page(page_id: str, title: str, path: str, text: str) -> dict[str, str]:
    return {
        "id": page_id,
        "title": title,
        "url": f"https://app.notion.com/p/{page_id.replace('-', '')}",
        "path": path,
        "text": text,
    }


TREK_NOTES = notion_page(
    "a1b2c3d4-0000-4000-8000-000000000001", "Trek notes", "Personal", "Venice is best in May."
)
HEALTH = notion_page("1a2b3c4d-1111-4222-8333-444455556666", "Health", "Personal", "")
VENICE_TRIP = notion_page(  # a page inside Trek notes: found by search, but not a database row
    "77aa77aa-1111-4222-8333-444455556666", "Venice trip", "Personal / Trek notes", "Book a cabin."
)
DATABASE = {
    "id": "b2c3d4e5-0000-4000-8000-000000000002",
    "title": "New database",
    "url": "https://app.notion.com/p/b2c3d4e5000040008000000000000002",
    "view": "view://b2c3d4e5-0000-4000-8000-000000000003",
}
NEW_PAGE_ID = "9f8e7d6c5b4a49388271605f4e3d2c1b"
NEW_PAGE_URL = f"https://app.notion.com/p/{NEW_PAGE_ID}"


class FakeNotion:
    """Stands in for Notion's MCP server: a real MCP server, in memory, with canned pages.

    Results copy the shapes Notion's real server returns (checked against the live server).
    `created`, `updated` and `moved` record every write.
    """

    def __init__(
        self, pages: list[dict[str, str]] | None = None, rows: list[dict[str, str]] | None = None
    ) -> None:
        self.pages = pages if pages is not None else [TREK_NOTES, HEALTH, VENICE_TRIP]
        self.rows = rows if rows is not None else [TREK_NOTES, HEALTH]  # the database's own pages
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.moved: list[dict[str, Any]] = []
        self.server = FastMCP("Fake Notion")
        self.add_tools()

    def client(self) -> Client:
        return Client(self.server)

    def add_tools(self) -> None:
        server = self.server

        @server.tool(name="notion-search")
        def search(query: str, page_size: int | None = None) -> str:
            words = query.lower().split()
            hits = [page for page in self.pages if any(w in page["title"].lower() for w in words)]
            results = [{key: page[key] for key in ("id", "title", "url", "path")} for page in hits]
            return json.dumps({"results": results, "type": "workspace_search"})

        @server.tool(name="notion-fetch")
        def fetch(id: str) -> str:
            if DATABASE["id"].replace("-", "") in id.replace("-", ""):  # its views, not its rows
                view = f'<views><view url="{{{{{DATABASE["view"]}}}}}"></view></views>'
                return json.dumps({"metadata": {"type": "database"}, **DATABASE, "text": view})
            page = next(p for p in self.pages if p["id"].replace("-", "") in id.replace("-", ""))
            return json.dumps({"metadata": {"type": "page"}, **page})

        @server.tool(name="notion-query-data-sources")
        def query_data_sources(data: dict[str, Any]) -> str:
            assert data["mode"] == "view" and data["view_url"] == DATABASE["view"]
            start = int(data.get("start_cursor", 0))
            page = self.rows[start : start + data["page_size"]]
            more = start + len(page) < len(self.rows)
            results = [{"Name": row["title"], "url": row["url"]} for row in page]
            return json.dumps(
                {"results": results, "has_more": more, "next_cursor": str(start + len(page))}
            )

        @server.tool(name="notion-create-pages")
        def create_pages(
            pages: list[dict[str, Any]],
            parent: dict[str, Any] | None = None,
            creation_mode: str | None = None,
        ) -> str:
            self.created.append({"pages": pages, "parent": parent, "creation_mode": creation_mode})
            return json.dumps({"pages": [{"id": NEW_PAGE_ID, "url": NEW_PAGE_URL}]})

        @server.tool(name="notion-update-page")
        def update_page(page_id: str, command: str, content: str | None = None) -> str:
            self.updated.append({"page_id": page_id, "command": command, "content": content})
            return json.dumps({"page_id": page_id})

        @server.tool(name="notion-move-pages")
        def move_pages(page_or_database_ids: list[str], new_parent: dict[str, Any]) -> str:
            self.moved.append({"ids": page_or_database_ids, "new_parent": new_parent})
            return json.dumps({"moved": page_or_database_ids})

        @server.tool(name="notion-duplicate-page")
        def duplicate_page(page_id: str) -> str:
            raise AssertionError("Jarvis is never given this tool")


# --- His voice (ElevenLabs) ---
class FakeVoice:
    """Stands in for ElevenLabs: every line "speaks" as 30 ms of quiet audio, or fails if told to.

    `lines` records what it was asked to say.
    """

    SAMPLE_RATE = 24000

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.lines: list[str] = []

    def synthesize(self, text: str) -> "FakeSpeech":
        self.lines.append(text)
        return FakeSpeech(self.fail)


class FakeSpeech:
    """What `synthesize()` returns: used with `async with`, then read frame by frame."""

    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def __aenter__(self) -> "FakeSpeech":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        if self.fail:
            raise RuntimeError("ElevenLabs is down.")
        for _ in range(3):  # three 10 ms frames
            samples = FakeVoice.SAMPLE_RATE // 100
            frame = rtc.AudioFrame(b"\x01\x00" * samples, FakeVoice.SAMPLE_RATE, 1, samples)
            yield SimpleNamespace(frame=frame)


class FakeEars:
    """Stands in for a Deepgram Flux stream: keeps the audio it's given; tests decide what it
    "hears" (`hears`) and whether it breaks (`fail`)."""

    def __init__(self) -> None:
        self.pushed: list[rtc.AudioFrame] = []
        self.start_time_offset = 0.0
        self.closed = False
        self.events: asyncio.Queue[stt.SpeechEvent | Exception | None] = asyncio.Queue()

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        if self.closed:
            raise RuntimeError("The stream is closed.")
        self.pushed.append(frame)

    def hears(self, *events: stt.SpeechEvent) -> None:
        for event in events:
            self.events.put_nowait(event)

    def fail(self, error: Exception) -> None:
        self.events.put_nowait(error)

    async def aclose(self) -> None:
        self.closed = True
        self.events.put_nowait(None)

    def __aiter__(self) -> "FakeEars":
        return self

    async def __anext__(self) -> stt.SpeechEvent:
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        if isinstance(event, Exception):
            self.closed = True
            raise event
        return event


class FakeSpotter:
    """Stands in for the wake-word models: a test frame carries the words "said" in it."""

    def hear(self, frame: rtc.AudioFrame) -> set[str]:
        return set(frame.userdata.get("words", ()))


class FakeFlux(stt.STT):
    """Stands in for Deepgram Flux inside a LiveKit session: each stream the gate opens is a
    `FakeEars`, kept in `streams`."""

    def __init__(self) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        self.streams: list[FakeEars] = []

    async def _recognize_impl(self, *args: Any, **kwargs: Any) -> stt.SpeechEvent:
        raise NotImplementedError("Flux only streams.")

    def stream(self, **kwargs: Any) -> FakeEars:  # type: ignore[override]
        self.streams.append(FakeEars())
        return self.streams[-1]


class FakeMic(io.AudioInput):
    """Your microphone inside a LiveKit session: tests `say` frames into it."""

    def __init__(self) -> None:
        super().__init__(label="Fake mic")
        self.frames: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()

    def say(self, *frames: rtc.AudioFrame) -> None:
        for frame in frames:
            self.frames.put_nowait(frame)

    async def __anext__(self) -> rtc.AudioFrame:
        return await self.frames.get()


# Mic frames and Flux's events, for the voice tests.
FRAME = 1 / 64  # seconds of audio per test frame (exact in binary, so sums of frames are too)


def frame(*words: str, loud: bool = False) -> rtc.AudioFrame:
    """1/64 s of mic audio, silent (or `loud`: someone talking), in which the fake spotter
    "hears" `words`."""
    audio = (8000).to_bytes(2, "little", signed=True) * 250 if loud else bytes(500)
    return rtc.AudioFrame(audio, 16000, 1, 250, userdata={"words": words})


def start() -> stt.SpeechEvent:
    return stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)


def interim(text: str, heard_up_to: float = 0.0) -> stt.SpeechEvent:
    """Flux's transcript so far; `heard_up_to`: how far into your audio it has got (seconds)."""
    kind = stt.SpeechEventType.INTERIM_TRANSCRIPT
    return stt.SpeechEvent(kind, alternatives=[stt.SpeechData("en", text, end_time=heard_up_to)])


def final(text: str, ended_at: float | None = None) -> stt.SpeechEvent:
    """Flux's final transcript; `ended_at`: when its last word ended (seconds into the session)."""
    kind = stt.SpeechEventType.FINAL_TRANSCRIPT
    words = None if ended_at is None else [io.TimedString(text, end_time=ended_at)]
    return stt.SpeechEvent(kind, alternatives=[stt.SpeechData("en", text, words=words)])


def end() -> stt.SpeechEvent:
    return stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH)
