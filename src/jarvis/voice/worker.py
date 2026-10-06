"""The voice worker: LiveKit runs `jarvis_session` each time a voice session starts.

Run it with LiveKit's CLI from the project folder (it finds `agent.py`):
- `jarvis up`: the desktop app, this worker and the local LiveKit server, together.
- `lk agent console`: talk to Jarvis in this terminal, with your mic and speakers.
- `lk agent debugger`: type turns from a script (no audio) and see what he did.
"""

import asyncio
import logging
import threading
import warnings
from datetime import datetime

from langsmith.run_trees import get_cached_client
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    room_io,
    tts,
)
from livekit.agents.utils.aio import cancel_and_wait

from jarvis.agents.registry import roll_call
from jarvis.brain.greeting import boot_greeting
from jarvis.brain.jarvis import make_brain
from jarvis.brain.memory import DB_FILE, open_memory
from jarvis.config import get_settings
from jarvis.voice import AGENT_NAME, SHORT_KEY_WARNING, quiet_livekit_exit_noise
from jarvis.voice.jarvis_agent import BrainLLM, JarvisAgent
from jarvis.voice.kokoro_tts import KokoroTTS, kokoro_for
from jarvis.voice.phrases import Phrases
from jarvis.voice.speech import make_ears, make_voice

logger = logging.getLogger(__name__)

# LiveKit runs agents with debug logs on, so these libraries log every request in full (aiosqlite
# even logs your conversation as it's saved), drowning out the lines that matter.
NOISY_LOGGERS = (
    *("aiosqlite", "anthropic", "firecrawl", "hpack", "httpcore", "httpcore2", "httpx", "httpx2"),
    *("keyring", "langsmith", "urllib3"),
)
for name in NOISY_LOGGERS:
    logging.getLogger(name).setLevel(logging.WARNING)
warnings.filterwarnings("ignore", SHORT_KEY_WARNING)  # the local server's dev secret
quiet_livekit_exit_noise()

TURNS = TurnHandlingOptions(
    turn_detection="stt",  # Flux's own end-of-turn model decides when you've finished
    endpointing={"min_delay": 0.3},  # then wait at most this long (seconds) before answering
    # Off: it starts answering before you've finished and throws wrong guesses away. Here each
    # guess would be a real Brain turn: saved to memory, maybe a sub-agent run.
    preemptive_generation={"enabled": False},
    # Stop the moment LiveKit hears you over him; the gate (gate.py) only lets "Jarvis…",
    # "stop" and "wait" through while he talks. LiveKit's default "pauses" him and resumes if no
    # words follow, but the console can't pause (it says it can, then does nothing).
    interruption={"resume_false_interruption": False},
)

TRACE_FLUSH_SECONDS = 5.0  # at a session's end, LangSmith gets this long to receive its traces

server = AgentServer(
    # One of you, so one session at a time: keep one process warm for the next one. LiveKit's
    # default keeps one per CPU core (14 here, ~1.6 GB of memory) for a server full of callers.
    num_idle_processes=1,
    # Never too busy for you. LiveKit's default turns new sessions away while the CPU is above
    # 70%: right for a fleet of servers, wrong for a Mac that's compiling something.
    load_fnc=lambda: 0.0,
)


@server.rtc_session(agent_name=AGENT_NAME)
async def jarvis_session(ctx: JobContext) -> None:
    """One voice session: open the memory, call the roll, greet you, then listen and talk."""
    settings = get_settings()
    memory = await open_memory(settings.data_dir / DB_FILE)
    ctx.add_shutdown_callback(memory.close)  # always close it: open connections block exiting
    ctx.add_shutdown_callback(send_last_traces)
    brain = make_brain(settings, memory)
    roll = await roll_call(settings)
    for name, health in roll.items():
        if not health.present:
            logger.warning("%s agent absent: %s", name, health.detail)
    greeting = boot_greeting(datetime.now(), sum(health.present for health in roll.values()))
    voice = make_voice(settings)
    phrases = Phrases(
        settings.data_dir / "voice" / "phrases",
        voice,  # recordings are always in his ElevenLabs voice, never the fallback's
        voice_name=f"{settings.elevenlabs_voice_id}/{settings.voice.tts_model}",
    )
    fallback = kokoro_for(settings)
    getting_ready = asyncio.create_task(get_ready(fallback))  # in the background: holds nothing up
    ctx.add_shutdown_callback(lambda: cancel_and_wait(getting_ready))
    session = AgentSession(
        stt=make_ears(settings),
        # No voice detector: it would let any voice (or his echo) stop him. The gate decides.
        vad=None,
        llm=BrainLLM(),  # a stand-in: replies come from the Brain, in JarvisAgent.llm_node
        # Kokoro speaks if ElevenLabs fails. Switch at once: retrying first meant ~6 s of silence
        # (measured); LiveKit keeps retrying ElevenLabs in the background and switches back.
        tts=tts.FallbackAdapter([voice, fallback], max_retry_per_tts=0),
        turn_handling=TURNS,
    )
    agent = JarvisAgent(brain, settings, greeting, phrases, room=ctx.room)
    await session.start(
        agent=agent,
        room=ctx.room,
        # Each desktop launch gets its own room. When the session ends (you quit, or it fails),
        # the room goes too, and a desktop that's still open starts a fresh one.
        room_options=room_io.RoomOptions(delete_room_on_close=True),
    )
    agent.serve_desktop()  # your clicks on the desktop's sign-in cards
    await agent.greet()  # only now: the audio output is final once start() returns


async def send_last_traces() -> None:
    """Send LangSmith the session's last traces, then stop its background threads: otherwise
    they'd be cut off as the session's process exits (and LiveKit warns about them)."""
    await asyncio.to_thread(stop_tracing)


def stop_tracing() -> None:
    get_cached_client().cleanup(TRACE_FLUSH_SECONDS)  # sends what's left, then tells them to stop
    for thread in threading.enumerate():  # they stop within a quarter of a second: wait for it
        if "tracing_control_thread" in thread.name:
            thread.join(timeout=1)


async def get_ready(fallback: KokoroTTS) -> None:
    """Download the fallback voice (first time only) and load it, so it can step in at once."""
    try:
        if not fallback.is_downloaded():
            await fallback.download()
        await asyncio.to_thread(fallback.load)
    except Exception as error:  # no fallback this session: ElevenLabs still speaks
        logger.warning("The offline fallback voice isn't available: %s", error)
