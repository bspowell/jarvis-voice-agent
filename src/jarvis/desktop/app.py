"""The desktop app (`jarvis desktop`). For now it's your voice line to Jarvis, with no window:
the orb arrives in Phase 10."""

import asyncio
import contextlib
import logging
import signal

from jarvis.config import Settings
from jarvis.desktop.room_client import RoomClient
from jarvis.voice import quiet_livekit_exit_noise


def run_desktop(settings: Settings) -> None:
    """Join Jarvis and stay until Ctrl+C (or SIGTERM, which `jarvis up` sends)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("livekit").setLevel(logging.WARNING)  # every connection step, otherwise
    quiet_livekit_exit_noise()
    with contextlib.suppress(KeyboardInterrupt, asyncio.CancelledError):
        asyncio.run(stay(settings))


async def stay(settings: Settings) -> None:
    """Stay with Jarvis; on SIGTERM, leave the room properly (so his session ends at once)."""
    task = asyncio.current_task()
    assert task is not None
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    await RoomClient(settings).run()
