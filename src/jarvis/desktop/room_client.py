"""The desktop app's line to Jarvis: your mic and speakers ⇄ the LiveKit server ⇄ the voice agent.

- Each launch joins a new room, with a token that invites Jarvis (dispatches the `jarvis` agent).
- Your mic goes through WebRTC's audio engine (`PlatformAudio`, as in Chrome calls): echo
  cancellation takes his voice back out of it, and his voice plays on your speakers by itself.
- The orb hears about everything he does: his state (listening / thinking / speaking), whether
  he's awake, and how loud his voice is. His display commands ("hide the orb") come in as RPCs.
- Watchdog: if he doesn't arrive, leaves, or the room ends (his session ended), it starts over
  in a new room. Today's conversation is in his memory, so nothing is lost.
"""

import asyncio
import logging
import time
import uuid
import warnings
from collections.abc import Coroutine, Mapping
from datetime import datetime
from typing import Any, Protocol

from livekit import api, rtc

from jarvis.config import Settings
from jarvis.display import AWAKE, DESKTOP, DISPLAY_METHOD, DisplayCommand
from jarvis.voice import AGENT_NAME, SHORT_KEY_WARNING
from jarvis.voice.audio import decibels

logger = logging.getLogger(__name__)

IDENTITY = DESKTOP  # you, in the room
ARRIVAL_SECONDS = 15.0  # how long he may take to arrive before the room is given up
RETRY_SECONDS = 2.0  # the pause before trying a new room
TRANSCRIPTION = "lk.transcription"  # LiveKit's topic for both sides' words
SESSION_EVENTS = "lk.agent.session"  # the agent's events for LiveKit's debugging tools: unused
AGENT_STATE = "lk.agent.state"  # his attribute in the room: listening / thinking / speaking
MIC = rtc.PlatformAudioOptions(  # WebRTC's processing of your mic, as in a browser call
    echo_cancellation=True, noise_suppression=True, auto_gain_control=True
)
VOICE_RATE = 24000  # his voice as it arrives (ElevenLabs and Kokoro both speak at 24 kHz)
LEVELS_PER_SECOND = 30  # how often the orb hears his loudness (it smooths in between)
QUIET_DB, LOUD_DB = -50.0, -12.0  # his voice's level: this quiet shows as 0, this loud as 1


class Orb(Protocol):
    """Where the line shows what Jarvis is doing: the overlay, in the desktop app."""

    def update(self, **fields: Any) -> None:
        """Any of `state`, `awake`, `level` changed. Called from the line's thread."""

    async def apply(self, command: DisplayCommand) -> str:
        """Show, hide or move the orb; returns what it did, in words."""


class NoOrb:
    """No window (e.g. in tests): nothing to show, and display commands say so."""

    def update(self, **fields: Any) -> None:
        pass

    async def apply(self, command: DisplayCommand) -> str:
        return "This desktop app has no orb to show."


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


def level(decibels: float) -> float:
    """His voice's loudness for the orb: 0 (quiet) to 1 (his loudest), in steps of 0.01."""
    share = (decibels - QUIET_DB) / (LOUD_DB - QUIET_DB)
    return round(min(1.0, max(0.0, share)), 2)


class RoomClient:
    """Keeps you in a room with Jarvis until cancelled: joins, sends your mic, plays him, shows
    him on the orb, and starts over in a new room whenever that one fails."""

    def __init__(self, settings: Settings, orb: Orb | None = None) -> None:
        self.settings = settings
        self.orb = orb or NoOrb()
        self.agent_state = "away"  # his state, as the room reports it
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
                self.he_is_here(participant)
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
            self.show_attributes(changed)

        @room.on("track_subscribed")
        def subscribed(
            track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.Participant
        ) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO and is_jarvis(participant):
                self.read(self.follow_voice(track))

        room.register_text_stream_handler(TRANSCRIPTION, self.show_words)
        room.register_byte_stream_handler(SESSION_EVENTS, self.skip)
        name = room_name(datetime.now())
        await room.connect(self.settings.livekit_url, invite(self.settings, name))
        room.local_participant.register_rpc_method(DISPLAY_METHOD, self.on_display)  # once in
        mic = audio.create_audio_source(MIC)
        try:
            track = rtc.LocalAudioTrack.create_audio_track("microphone", mic)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track, options)
            logger.info("Joined %s with your mic; waiting for Jarvis…", name)
            for participant in room.remote_participants.values():
                if is_jarvis(participant):
                    self.he_is_here(participant)
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
            self.orb.update(state="away", awake=False, level=0)
            await room.disconnect()
            mic.close()

    def he_is_here(self, participant: rtc.RemoteParticipant) -> None:
        """He has just arrived: show the state he came with."""
        self.show_attributes(participant.attributes)

    def show_attributes(self, changed: Mapping[str, str]) -> None:
        """His state (LiveKit's attribute) and whether he's awake (ours) → the orb."""
        if AGENT_STATE in changed:
            self.agent_state = changed[AGENT_STATE]
            logger.debug("Jarvis is %s.", self.agent_state)
            self.orb.update(state=self.agent_state)
        if AWAKE in changed:
            self.orb.update(awake=changed[AWAKE] == "true")

    async def follow_voice(self, track: rtc.Track) -> None:
        """His voice's loudness → the orb, at most 30 times a second, and only when it changes.
        PlatformAudio plays the same track on your speakers; this only listens in."""
        stream = rtc.AudioStream(track, sample_rate=VOICE_RATE, num_channels=1)
        loudest, shown, sent_at = 0.0, 0.0, 0.0
        try:
            async for event in stream:
                loudest = max(loudest, level(decibels(event.frame)))
                if time.monotonic() - sent_at < 1 / LEVELS_PER_SECOND:
                    continue
                if loudest != shown:  # his silence would be 30 zeros a second
                    self.orb.update(level=loudest)
                    shown = loudest
                loudest, sent_at = 0.0, time.monotonic()
        finally:
            await stream.aclose()
            self.orb.update(level=0)

    async def on_display(self, call: rtc.RpcInvocationData) -> str:
        """Jarvis asks to show, hide or move the orb; the answer says what was done."""
        try:
            command = DisplayCommand.from_json(call.payload)
        except (ValueError, KeyError, TypeError) as error:
            raise rtc.RpcError(rtc.RpcError.ErrorCode.APPLICATION_ERROR, str(error)) from None
        done = await self.orb.apply(command)
        logger.info("Orb: %s", done)
        return done

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


def is_jarvis(participant: rtc.Participant) -> bool:
    return participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT


async def drain(reader: rtc.ByteStreamReader) -> None:
    async for _ in reader:
        pass
