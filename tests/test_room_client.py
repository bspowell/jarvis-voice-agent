"""The desktop's line to Jarvis: the room it joins, its token, and what it tells the orb."""

from datetime import datetime

import pytest
from livekit import api, rtc

from jarvis.config import Settings
from jarvis.desktop.room_client import AGENT_STATE, IDENTITY, RoomClient, invite, level, room_name
from jarvis.display import AWAKE, DISPLAY_METHOD, SIGN_IN_METHOD, DisplayCommand
from jarvis.events import Event
from jarvis.voice import AGENT_NAME


def test_each_launch_gets_a_room_of_its_own():
    now = datetime(2026, 10, 5, 16, 12)
    first, second = room_name(now), room_name(now)
    assert first.startswith("jarvis-1005-1612-")
    assert first != second  # two launches in the same minute still differ


def test_the_token_lets_you_in_and_invites_jarvis():
    settings = Settings(_env_file=None, livekit_api_key="devkey", livekit_api_secret="secret")
    token = invite(settings, "jarvis-1005-1612-3fa2")
    claims = api.TokenVerifier("devkey", "secret").verify(token)  # signed with the server's key
    assert claims.identity == IDENTITY
    assert claims.video.room_join and claims.video.room == "jarvis-1005-1612-3fa2"
    assert [agent.agent_name for agent in claims.room_config.agents] == [AGENT_NAME]


# --- The orb ---
class FakeOrb:
    """Records what the orb was shown and asked to do."""

    def __init__(self) -> None:
        self.shown: list[dict] = []
        self.commands: list[DisplayCommand] = []
        self.events: list[Event] = []  # what reached the cards

    def update(self, **fields) -> None:
        self.shown.append(fields)

    async def apply(self, command: DisplayCommand) -> str:
        self.commands.append(command)
        return "The orb is hidden."

    def show_event(self, event: Event) -> None:
        self.events.append(event)


def client_with(orb: FakeOrb) -> RoomClient:
    return RoomClient(Settings(_env_file=None), orb=orb)


def display_call(payload: str) -> rtc.RpcInvocationData:
    return rtc.RpcInvocationData("call-1", "agent-1", payload, 3.0, DISPLAY_METHOD)


def test_his_voice_level_runs_from_quiet_to_his_loudest():
    assert level(-80) == level(-50) == 0
    assert level(-31) == 0.5
    assert level(-12) == level(0) == 1


def test_the_orb_shows_his_state_and_whether_he_is_awake():
    orb = FakeOrb()
    client = client_with(orb)
    client.show_attributes({AGENT_STATE: "thinking"})
    client.show_attributes({AWAKE: "true", "someone.else": "x"})
    client.show_attributes({AWAKE: "false"})
    assert orb.shown == [{"state": "thinking"}, {"awake": True}, {"awake": False}]
    assert client.agent_state == "thinking"


async def test_display_commands_reach_the_orb_and_its_answer_goes_back():
    orb = FakeOrb()
    answer = await client_with(orb).on_display(display_call(DisplayCommand("hide").to_json()))
    assert answer == "The orb is hidden."
    assert orb.commands == [DisplayCommand("hide")]


async def test_a_garbled_display_command_is_refused():
    with pytest.raises(rtc.RpcError):
        await client_with(FakeOrb()).on_display(display_call('{"action": "explode"}'))


# --- The cards ---
class FakeReader:
    """A text stream that has arrived in full."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def read_all(self) -> str:
        return self.text


class FakeLine:
    """The room as the desktop sees it: its own side, which calls Jarvis."""

    def __init__(self, answer: str | Exception = "Waiting for you.") -> None:
        self.local_participant = self
        self.answer = answer
        self.calls: list[dict] = []

    async def perform_rpc(self, **call) -> str:
        self.calls.append(call)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def test_his_brains_events_reach_the_cards():
    orb = FakeOrb()
    client = client_with(orb)
    weather = Event("weather", {"place": "Vancouver"})
    await client.read_event(FakeReader(weather.to_json()))
    await client.read_event(FakeReader("garbled"))  # logged and dropped
    assert orb.events == [weather]


async def test_a_click_on_sign_in_asks_jarvis_to_open_the_browser():
    orb = FakeOrb()
    client = client_with(orb)
    client.room, client.jarvis = FakeLine(), "agent-AJ_1"
    await client.ask_to_sign_in("google")
    assert client.room.calls == [
        {
            "destination_identity": "agent-AJ_1",
            "method": SIGN_IN_METHOD,
            "payload": "google",
            "response_timeout": 3.0,
        }
    ]
    assert orb.events == [Event("signing_in", {"service": "google"})]  # "finish in the browser"


async def test_a_click_that_cannot_reach_jarvis_says_so_on_the_card():
    orb = FakeOrb()
    client = client_with(orb)
    await client.ask_to_sign_in("google")  # he isn't in the room
    timeout = rtc.RpcError(rtc.RpcError.ErrorCode.RESPONSE_TIMEOUT, "Response timeout")
    client.room, client.jarvis = FakeLine(answer=timeout), "agent-AJ_1"
    await client.ask_to_sign_in("notion")
    assert orb.events == [
        Event("sign_in_failed", {"service": "google"}),
        Event("sign_in_failed", {"service": "notion"}),
    ]
