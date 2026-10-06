"""The desktop app (`jarvis desktop`): the orb on your screen, and your voice line to Jarvis.

macOS runs windows on the main thread only, and its event loop keeps that thread busy. So:
- main thread: the orb's window (overlay.py) and macOS's event loop
- the line's thread: an asyncio loop running the room client (mic, speakers, orb updates)

Ctrl+C or SIGTERM (`jarvis up` sends it) hangs up first, so his session ends at once; when the
line has ended, for that or any other reason, the window closes and the app exits.
"""

import asyncio
import contextlib
import logging
import signal
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import AppKit
from PyObjCTools import AppHelper, MachSignals

from jarvis.config import Settings
from jarvis.desktop.demo import pretend_sign_in, show_off
from jarvis.desktop.overlay import Overlay
from jarvis.desktop.room_client import RoomClient
from jarvis.voice import quiet_livekit_exit_noise

logger = logging.getLogger(__name__)

HANG_UP_SECONDS = 10.0  # leaving the room takes well under a second; this is a backstop
ORB_MEMORY = "orb.json"  # in data/: where the orb was, and what's in its middle


class Line:
    """A coroutine (the room client's `run`) on a thread of its own, with its own asyncio loop."""

    def __init__(
        self, call: Coroutine[Any, Any, None], when_over: Callable[[], None] = lambda: None
    ) -> None:
        self.loop = asyncio.new_event_loop()
        self.task = self.loop.create_task(call)
        self.when_over = when_over  # runs on the line's thread once the call has ended
        self.thread = threading.Thread(target=self.run, name="line-to-jarvis")

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass  # hung up
        except Exception:
            logger.exception("The line to Jarvis broke.")
        finally:
            self.loop.close()
            self.when_over()

    def hang_up(self) -> None:
        """End the call. Safe from any thread, and more than once."""
        with contextlib.suppress(RuntimeError):  # the loop has already closed
            self.loop.call_soon_threadsafe(self.task.cancel)

    def wait(self, seconds: float = HANG_UP_SECONDS) -> None:
        self.thread.join(seconds)


def run_desktop(settings: Settings, demo: bool = False) -> None:
    """Show the orb, join Jarvis, and stay until Ctrl+C or SIGTERM. With `demo`, the orb plays
    through its looks instead (no Jarvis needed)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("livekit").setLevel(logging.WARNING)  # every connection step, otherwise
    quiet_livekit_exit_noise()
    app = AppKit.NSApplication.sharedApplication()
    # An accessory app: no Dock icon, no menu bar, never takes your keyboard focus, and its
    # windows may float over other apps' fullscreen windows.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    overlay = Overlay(memory=settings.data_dir / ORB_MEMORY)
    overlay.show()
    if demo:
        overlay.cards.on_sign_in = lambda service: pretend_sign_in(overlay, service)
        call = show_off(overlay)
    else:
        client = RoomClient(settings, orb=overlay)
        overlay.cards.on_sign_in = client.sign_in_clicked  # a card's "Sign in" → ask Jarvis
        call = client.run()
    line = Line(call, when_over=lambda: AppHelper.callAfter(stop))
    for signum in (signal.SIGINT, signal.SIGTERM):
        # Python's own handlers would wait for the event loop to hand back the main thread;
        # PyObjC delivers the signal through the event loop itself.
        MachSignals.signal(signum, lambda _signum: line.hang_up())
    line.start()
    try:
        app.run()  # until `stop`, once the line has ended
    finally:
        line.hang_up()
        line.wait()


def stop() -> None:
    """End macOS's event loop (main thread). It only notices after an event: send an empty one."""
    app = AppKit.NSApplication.sharedApplication()
    app.stop_(None)
    nudge = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(  # noqa: E501 (one long macOS name)
        AppKit.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0
    )
    app.postEvent_atStart_(nudge, True)
