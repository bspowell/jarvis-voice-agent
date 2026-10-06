"""The desktop app's line to Jarvis: your mic and speakers ⇄ the LiveKit server ⇄ the voice agent.

- Each launch joins a new room, with a token that invites Jarvis (dispatches the `jarvis` agent).
- Your mic goes through WebRTC's audio engine (`PlatformAudio`, as in Chrome calls): echo
  cancellation takes his voice back out of it, and his voice plays on your speakers by itself.
- Watchdog: if he doesn't arrive, leaves, or the room ends (his session ended), it starts over
  in a new room. Today's conversation is in his memory, so nothing is lost.
"""

import asyncio
import logging
import uuid
import warnings
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from livekit import api, rtc

from jarvis.config import Settings
from jarvis.voice import AGENT_NAME, SHORT_KEY_WARNING

logger = logging.getLogger(__name__)

IDENTITY = "desktop"  # you, in the room
ARRIVAL_SECONDS = 15.0  # how long he may take to arrive before the room is given up
RETRY_SECONDS = 2.0  # the pause before trying a new room
TRANSCRIPTION = "lk.transcription"  # LiveKit's topic for both sides' words
SESSION_EVENTS = "lk.agent.session"  # the agent's events for LiveKit's debugging tools: unused
AGENT_STATE = "lk.agent.state"  # his attribute in the room: listening / thinking / speaking
MIC = rtc.PlatformAudioOptions(  # WebRTC's processing of your mic, as in a browser call
    echo_cancellation=True, noise_suppression=True, auto_gain_control=True
)


def room_name(now: datetime) -> str:
    """A new room for each launch, e.g. "jarvis-1005-1612-3fa2"."""
    return f"jarvis-{now:%m%d-%H%M}-{uuid.uuid4().hex[:4]}"


def invite(settings: Settings, room: str) -> str:
    """A token that lets you into `room` and invites Jarvis when the room is created."""
    dispatch = api.RoomAgentDispatch(agent_name=AGENT_NAME)
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret.get_secret_value())
        .with_identity(IDENTITY)
        .with_name("You")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_room_config(api.RoomConfiguration(agents=[dispatch]))
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", SHORT_KEY_WARNING)  # the local server's dev secret
        return token.to_jwt()


class RoomClient:
    """Keeps you in a room with Jarvis until cancelled: joins, sends your mic, plays him, and
    starts over in a new room whenever that one fails."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.agent_state = "away"  # his state, as the room reports it (the orb will show it)
        self.reading: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        audio = rtc.PlatformAudio()  # your Mac's mic and speakers, through WebRTC
        try:
            while True:
                try:
                    await self.visit(audio)
                except Exception as error:  # e.g. the server isn't running
                    logger.warning("No line to Jarvis: %s", error)
                await asyncio.sleep(RETRY_SECONDS)
        finally:
            audio.close()

    async def visit(self, audio: rtc.PlatformAudio) -> None:
        """One room: join, send your mic, wait for him, stay until he or the room goes."""
        room = rtc.Room()
        here, over = asyncio.Event(), asyncio.Event()

        @room.on("participant_connected")
        def arrived(participant: rtc.RemoteParticipant) -> None:
            if is_jarvis(participant):
                logger.info("Jarvis is here.")
                here.set()

        @room.on("participant_disconnected")
        def left(participant: rtc.RemoteParticipant) -> None:
            if is_jarvis(participant):
                logger.info("Jarvis left the room.")
                over.set()

        @room.on("disconnected")
        def disconnected(reason: rtc.DisconnectReason.ValueType) -> None:
            logger.info("The room closed (%s).", rtc.DisconnectReason.Name(reason))
            over.set()

        @room.on("participant_attributes_changed")
        def attributes(changed: dict[str, str], participant: rtc.Participant) -> None:
            if AGENT_STATE in changed:
                self.agent_state = changed[AGENT_STATE]
                logger.debug("Jarvis is %s.", self.agent_state)

        room.register_text_stream_handler(TRANSCRIPTION, self.show_words)
        room.register_byte_stream_handler(SESSION_EVENTS, self.skip)
        name = room_name(datetime.now())
        await room.connect(self.settings.livekit_url, invite(self.settings, name))
        mic = audio.create_audio_source(MIC)
        try:
            track = rtc.LocalAudioTrack.create_audio_track("microphone", mic)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track, options)
            logger.info("Joined %s with your mic; waiting for Jarvis…", name)
            if any(is_jarvis(p) for p in room.remote_participants.values()):
                here.set()
            try:
                await asyncio.wait_for(here.wait(), ARRIVAL_SECONDS)
            except TimeoutError:
                raise RuntimeError(
                    f"Jarvis didn't arrive within {ARRIVAL_SECONDS:g} s. Is the voice agent "
                    "running? (`jarvis up` starts everything)"
                ) from None
            await over.wait()
        finally:
            self.agent_state = "away"
            await room.disconnect()
            mic.close()

    def show_words(self, reader: rtc.TextStreamReader, identity: str) -> None:
        """What you said and what he said, as LiveKit transcribes them (final versions only)."""
        self.read(self.read_words(reader, identity))

    def skip(self, reader: rtc.ByteStreamReader, identity: str) -> None:
        self.read(drain(reader))  # read and dropped, so it doesn't pile up

    def read(self, reading: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(reading)
        self.reading.add(task)
        task.add_done_callback(self.reading.discard)

    async def read_words(self, reader: rtc.TextStreamReader, identity: str) -> None:
        words = await reader.read_all()
        if reader.info.attributes.get("lk.transcription_final") == "true" and words.strip():
            logger.info("%s: %s", "You" if identity == IDENTITY else "Jarvis", words.strip())


def is_jarvis(participant: rtc.RemoteParticipant) -> bool:
    return participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT


async def drain(reader: rtc.ByteStreamReader) -> None:
    async for _ in reader:
        pass
