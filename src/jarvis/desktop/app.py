"""The desktop app (`jarvis desktop`; Jarvis.app opens it): Jarvis's menu-bar icon, the orb, and
your voice line to him.

When it opens, only the icon shows: Jarvis is off (no orb, no mic). Start him from its menu or
with ⌃⌥J (or tick "Start Jarvis Automatically"): the app starts the LiveKit server and the voice
agent (up.py), shows the orb and joins his room with your mic (controls.py). "Stop Jarvis" undoes
it; "Quit Jarvis" also closes the app. `jarvis up` is this app, starting him at once.

macOS runs windows on the main thread only, and its event loop keeps that thread busy. So:
- main thread: the icon and its menu, the orb's window, the shortcuts, macOS's event loop
- the line's thread (line.py): an asyncio loop for everything that waits: the crew, the room
  with Jarvis, sign-ins

Ctrl+C, SIGTERM (macOS at log out; Jarvis.app passes it on) or closing the terminal stop him
first, then close the app; a second one kills the crew at once.
"""

import fcntl
import logging
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO

import AppKit
import Foundation
from PyObjCTools import AppHelper, MachSignals

from jarvis import up
from jarvis.auth.accounts import Service
from jarvis.config import Settings
from jarvis.desktop import install
from jarvis.desktop.controls import Controls
from jarvis.desktop.demo import pretend_sign_in, show_off
from jarvis.desktop.hotkeys import HotKeys
from jarvis.desktop.line import Line
from jarvis.desktop.menu import MUTE_KEY, TALK_KEY, Snapshot, connection_of, mood
from jarvis.desktop.menu_bar import MenuBar
from jarvis.desktop.overlay import Overlay
from jarvis.display import DisplayCommand
from jarvis.voice import quiet_livekit_exit_noise

logger = logging.getLogger(__name__)

ORB_MEMORY = "orb.json"  # in data/: where the orb was, and what's in its middle
LOCK = "desktop.lock"  # in data/: held while the app runs
MENU_FIELDS = {"state", "awake", "muted"}  # the orb updates that change the icon
QUIT_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
ALREADY_RUNNING = "Jarvis is already running: his icon is in the menu bar (Quit Jarvis first)."


def run_desktop(settings: Settings, start_now: bool = False, demo: bool = False) -> int:
    """Run the app until you quit it; returns its exit code. `start_now`: start Jarvis at once
    (`jarvis up`). `demo`: the orb plays through its looks instead (no Jarvis needed)."""
    lock = None if demo else hold_lock(settings.data_dir / LOCK)
    if lock is None and not demo:  # before opening the log: it's the running app's
        print(ALREADY_RUNNING, file=sys.stderr)
        return 0
    up.open_log(settings, in_terminal=sys.stdout.isatty())
    up.tag_log_messages()
    logging.getLogger("livekit").setLevel(logging.WARNING)  # every connection step, otherwise
    quiet_livekit_exit_noise()
    app = AppKit.NSApplication.sharedApplication()
    # An accessory app: no Dock icon, never takes your keyboard focus, and its windows may float
    # over other apps' fullscreen windows. Its one way in is the menu-bar icon.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    if lock is None:
        return run_demo(settings)
    up.clean_up_leftovers(settings)
    line = Line()
    line.start()
    overlay = Overlay(memory=settings.data_dir / ORB_MEMORY)  # hidden until he starts
    controls = Controls(settings, overlay, line, on_main=AppHelper.callAfter)
    overlay.cards.on_sign_in = controls.sign_in_clicked  # a card's "Sign in"

    def quit_app(exit_code: int = 0) -> None:
        up.say("Quitting.")
        controls.quit(exit_code).add_done_callback(lambda _: AppHelper.callAfter(stop))

    menu = MenuBar(
        lambda full: snapshot(settings, controls, overlay, full),
        actions={
            "start": lambda _: controls.start(),
            "stop": lambda _: controls.stop(),
            "talk": lambda _: controls.talk(),
            "mute": lambda _: controls.toggle_mute(),
            "orb": lambda _: toggle_orb(overlay),
            "core": lambda core: overlay.set_core(core),
            "start_option": lambda option: choose_start_option(controls, option),
            "sign_in": lambda service: controls.sign_in(Service(service)),
            "sign_out": lambda service: controls.sign_out(Service(service)),
            "show_log": lambda _: show_file(settings.data_dir / up.LOG),
            "quit": lambda _: quit_app(),
        },
    )
    controls.watchers.append(menu.refresh)
    overlay.watchers.append(lambda fields: menu.refresh() if MENU_FIELDS & fields.keys() else None)
    awake = NoNap()
    controls.watchers.append(lambda: awake.keep(controls.state != "off"))
    keys = HotKeys()
    keys.add(TALK_KEY, controls.talk)
    keys.add(MUTE_KEY, controls.toggle_mute)
    stop_on_signals(quit_app, force=lambda: force_quit(controls))
    up.say("Jarvis's icon is in the menu bar." + (" Ctrl+C quits." if up.echo else ""))
    if start_now or controls.remembered.start_automatically:
        controls.start()
    try:
        app.run()  # until `stop`, once Jarvis has stopped
    finally:
        keys.close()
        menu.close()
        line.close()
        up.close_log()
        lock.close()
    return controls.exit_code


def run_demo(settings: Settings) -> int:
    """`jarvis desktop --demo`: the cards, then the orb's every look, until Ctrl+C."""
    overlay = Overlay(memory=settings.data_dir / ORB_MEMORY)
    overlay.show()
    overlay.cards.on_sign_in = lambda service: pretend_sign_in(overlay, service)
    line = Line()
    line.start()
    showing = line.submit(show_off(overlay))
    showing.add_done_callback(lambda _: AppHelper.callAfter(stop))
    stop_on_signals(lambda exit_code: showing.cancel(), force=lambda: os._exit(130))
    try:
        app = AppKit.NSApplication.sharedApplication()
        app.run()
    finally:
        line.close()
        up.close_log()
    return 0


def snapshot(
    settings: Settings, controls: Controls, overlay: Overlay, full: bool = True
) -> Snapshot:
    """How things are, for the menu and the icon (main thread). `full`: with the accounts,
    from the Keychain (only the open menu shows them)."""
    return Snapshot(
        state=controls.state,
        mood=mood(overlay.latest),
        muted=controls.muted,
        problem=controls.problem,
        orb_showing=bool(overlay.window.isVisible()),
        core=overlay.core,
        connections=tuple(
            connection_of(settings, service, service in controls.signing_in)
            for service in (Service if full else ())
        ),
        start_automatically=controls.remembered.start_automatically,
        open_at_login=install.opens_at_login(),
    )


def choose_start_option(controls: Controls, option: str | None) -> None:
    """At login: "launch" (the icon, and Jarvis starts), "menu_bar" (just the icon) or "off"."""
    install.set_open_at_login(option != "off")
    controls.set_start_automatically(option == "launch")


def toggle_orb(overlay: Overlay) -> None:
    command = DisplayCommand("hide" if overlay.window.isVisible() else "show")
    logger.info("Orb: %s", overlay.carry_out(command))


def show_file(path: Path) -> None:
    """Open a file in its usual app (the log: Console, which follows it as it grows)."""
    AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.fileURLWithPath_(str(path)))


class NoNap:
    """While Jarvis is on, macOS mustn't nap the app (it slows hidden apps' timers and network,
    and the orb can be hidden); the Mac itself may still sleep."""

    def __init__(self) -> None:
        self.activity: object | None = None

    def keep(self, awake: bool) -> None:
        info = Foundation.NSProcessInfo.processInfo()
        if awake and self.activity is None:
            self.activity = info.beginActivityWithOptions_reason_(
                Foundation.NSActivityUserInitiatedAllowingIdleSystemSleep,
                "Jarvis is listening for “Hey Jarvis”.",
            )
        elif not awake and self.activity is not None:
            info.endActivity_(self.activity)
            self.activity = None


def hold_lock(path: Path) -> IO[str] | None:
    """One desktop app at a time: two would both hear you, and Jarvis would answer twice. The
    lock is held while the file stays open; None if another app holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+")
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        file.close()
        return None
    file.seek(0)
    file.truncate()
    file.write(f"{os.getpid()}\n")
    file.flush()
    return file


def stop_on_signals(quit_app: Callable[[int], None], force: Callable[[], None]) -> None:
    """The first Ctrl+C / SIGTERM / SIGHUP stops Jarvis, then the app; a second, at once.
    Python's own handlers would wait for the event loop to hand back the main thread; PyObjC
    delivers signals through the event loop itself."""
    received: list[int] = []

    def on_signal(signum: int) -> None:
        received.append(signum)
        if len(received) > 1:
            force()
        else:
            quit_app(130 if signum == signal.SIGINT else 0)

    for signum in QUIT_SIGNALS:
        MachSignals.signal(signum, on_signal)


def force_quit(controls: Controls) -> None:
    up.say("Stopping at once.")
    controls.kill()
    os._exit(130)


def stop() -> None:
    """End macOS's event loop (main thread). It only notices after an event: send an empty one."""
    app = AppKit.NSApplication.sharedApplication()
    app.stop_(None)
    nudge = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(  # noqa: E501 (one long macOS name)
        AppKit.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0
    )
    app.postEvent_atStart_(nudge, True)
