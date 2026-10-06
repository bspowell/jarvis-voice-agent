"""The desktop's line to Jarvis: the room it joins, and the invitation in its token."""

from datetime import datetime

from livekit import api

from jarvis.config import Settings
from jarvis.desktop.room_client import IDENTITY, invite, room_name
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
