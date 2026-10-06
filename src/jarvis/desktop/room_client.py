"""The desktop app's line to Jarvis: your mic and speakers ⇄ the LiveKit server ⇄ the voice agent.

- Each launch joins a new room, with a token that invites Jarvis (dispatches the `jarvis` agent).
- Your mic goes through WebRTC's audio engine (`PlatformAudio`, as in Chrome calls): echo
  cancellation takes his voice back out of it, and his voice plays on your speakers by itself.
- The orb hears about everything he does: his state (listening / thinking / speaking), whether
  he's awake, and how loud his voice is. His display commands ("hide the orb") come in as RPCs.
- His Brain's events come in as text: the cards beside the orb (the weather, the agenda…). Your
  click on a sign-in card goes back to him as an RPC: he opens the browser.
- Mute: the mic track is muted, and the Mac stops recording (its orange mic dot goes out). The
  talk shortcut is an RPC too: he listens to you now (he wakes, or stops talking).
- Watchdog: if he doesn't arrive, leaves, or the room ends (his session ended), it starts over
  in a new room. Today's conversation is in his memory, so nothing is lost.
"""

import asyncio
import contextlib
import logging
import time
import uuid
import warnings
from collections.abc import Coroutine, Mapping
from datetime import datetime
from typing import Any, Protocol

from livekit import api, rtc

from jarvis.config import Settings
from jarvis.display import (
    AWAKE,
    DESKTOP,
    DISPLAY_METHOD,
    EVENTS_TOPIC,
    SIGN_IN_METHOD,
    TALK_METHOD,
    DisplayCommand,
)
from jarvis.events import Event
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
SIGN_IN_SECONDS = 3.0  # how long he may take to answer a click on "Sign in"
TALK_SECONDS = 3.0  # how long he may take to answer the talk shortcut


class Orb(Protocol):
    """Where the line shows what Jarvis is doing: the overlay, in the desktop app."""

    def update(self, **fields: Any) -> None:
        """Any of `state`, `awake`, `level`, `muted` changed. Called from the line's thread."""

    async def apply(self, command: DisplayCommand) -> str:
        """Show, hide or move the orb; returns what it did, in words."""

    def show_event(self, event: Event) -> None:
        """Something happened that may deserve a card (the weather, the agenda…). Called from
        the line's thread."""


class NoOrb:
    """No window (e.g. in tests): nothing to show, and display commands say so."""

    def update(self, **fields: Any) -> None:
        pass

    async def apply(self, command: DisplayCommand) -> str:
        return "This desktop app has no orb to show."

    def show_event(self, event: Event) -> None:
        pass


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
        self.room: rtc.Room | None = None  # the room you're in, if any
        self.jarvis: str | None = None  # his identity in it, once he's there
        self.loop: asyncio.AbstractEventLoop | None = None  # the line's (for clicks)
        self.muted = False  # your mic, by your choice (kept when the watchdog starts a new room)
        self.mic_track: rtc.LocalAudioTrack | None = None  # your mic in the room you're in

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.orb.update(muted=self.muted)
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
                self.jarvis = None
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
        room.register_text_stream_handler(EVENTS_TOPIC, self.show_event)
        room.register_byte_stream_handler(SESSION_EVENTS, self.skip)
        name = room_name(datetime.now())
        await room.connect(self.settings.livekit_url, invite(self.settings, name))
        self.room = room
        room.local_participant.register_rpc_method(DISPLAY_METHOD, self.on_display)  # once in
        mic = audio.create_audio_source(MIC)
        try:
            track = rtc.LocalAudioTrack.create_audio_track("microphone", mic)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track, options)
            self.mic_track = track
            if self.muted:
                track.mute()
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
            self.room = self.jarvis = self.mic_track = None
            self.orb.update(state="away", awake=False, level=0)
            await room.disconnect()
            mic.close()

    def he_is_here(self, participant: rtc.RemoteParticipant) -> None:
        """He has just arrived: show the state he came with."""
        self.jarvis = participant.identity
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

    def show_event(self, reader: rtc.TextStreamReader, identity: str) -> None:
        """One of his Brain's events: the orb's cards show some (the weather, the agenda…)."""
        self.read(self.read_event(reader))

    async def read_event(self, reader: rtc.TextStreamReader) -> None:
        text = await reader.read_all()
        try:
            event = Event.from_json(text)
        except ValueError:
            logger.warning("Not an event: %s", text[:80])
            return
        self.orb.show_event(event)

    def sign_in_clicked(self, service: str) -> None:
        """You clicked "Sign in" on a card. Safe from any thread (the click comes from the
        window's): the call to Jarvis runs on the line's own loop."""
        if self.loop is not None:
            with contextlib.suppress(RuntimeError):  # the line has ended
                asyncio.run_coroutine_threadsafe(self.ask_to_sign_in(service), self.loop)

    async def ask_to_sign_in(self, service: str) -> None:
        """Ask Jarvis to open the sign-in page in your browser; the card shows how it went."""
        room, jarvis = self.room, self.jarvis
        try:
            if room is None or jarvis is None:
                raise RuntimeError("Jarvis isn't connected.")
            await room.local_participant.perform_rpc(
                destination_identity=jarvis,
                method=SIGN_IN_METHOD,
                payload=service,
                response_timeout=SIGN_IN_SECONDS,
            )
        except Exception as error:  # he's gone, timed out, or refused: the card says so
            logger.warning("The %s sign-in didn't start: %s", service, error)
            self.orb.show_event(Event("sign_in_failed", {"service": service}))
            return
        self.orb.show_event(Event("signing_in", {"service": service}))

    def mute(self, muted: bool) -> None:
        """Mute or unmute your mic. Muted, the Mac stops recording (measured: muting the track
        stops WebRTC's recording; taking the track out of the room doesn't) and Jarvis hears
        silence. Call it on the line's loop."""
        self.muted = muted
        if self.mic_track is not None:
            if muted:
                self.mic_track.mute()
            else:
                self.mic_track.unmute()
        self.orb.update(muted=muted)

    async def talk(self) -> None:
        """The talk shortcut: ask Jarvis to listen to you now (he wakes, or stops talking)."""
        room, jarvis = self.room, self.jarvis
        if room is None or jarvis is None:
            logger.info("Jarvis isn't in the room yet.")
            return
        try:
            await room.local_participant.perform_rpc(
                destination_identity=jarvis,
                method=TALK_METHOD,
                payload="",
                response_timeout=TALK_SECONDS,
            )
        except Exception as error:  # he's gone or busy: he'll still hear "Hey Jarvis"
            logger.warning("Jarvis didn't get the talk shortcut: %s", error)

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
