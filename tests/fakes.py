"""Stand-ins for the model and the web, so tests are fast, free and offline."""

import re
from collections.abc import Iterator
from typing import Any

import httpx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk


class FakeToolCallingModel(GenericFakeChatModel):
    """Scripted replies that may "call" tools: lets tests run the whole agent loop.

    LangChain's `GenericFakeChatModel` can't accept tools or stream tool calls; this adds both.
    """

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolCallingModel":
        return self  # the script already says which tools to call

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
