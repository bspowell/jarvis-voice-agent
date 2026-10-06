"""What the menu-bar icon and the shortcuts do: start and stop Jarvis, mute your mic, call him,
sign in and out of Google and Notion.

- Start: the crew (up.py: the LiveKit server, then the voice agent), then the orb appears and
  the room client joins his room with your mic. He greets you.
- Stop: the same in reverse. The mic is released, so macOS's orange mic dot goes out.
- If a part stops by itself, he stops too, and the menu says why.

Called from the main thread (the menu, the shortcuts, the cards' buttons): the work runs on the
line's asyncio loop (line.py), where Jarvis's state also changes, so changes never race.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from jarvis.auth import google, notion
from jarvis.auth.accounts import ACCOUNTS, Service, sign_in
from jarvis.config import Settings
from jarvis.desktop.line import Line
from jarvis.desktop.room_client import RoomClient
from jarvis.display import DisplayCommand
from jarvis.events import Event
from jarvis.up import Crew, PartFailed, say

logger = logging.getLogger(__name__)

State = Literal["off", "starting", "on", "stopping"]
MEMORY = "app.json"  # in data/: the menu's settings
BOUNCE_SECONDS = 0.3  # a shortcut pressed while its menu is open arrives twice: count it once


@dataclass
class Remembered:
    """What the menu keeps between launches."""

    start_automatically: bool = False  # start Jarvis as soon as the app opens (e.g. at login)

    @classmethod
    def load(cls, path: Path) -> "Remembered":
        try:
            saved = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            saved = {}
        return cls(start_automatically=saved.get("start_automatically") is True)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


class Window(Protocol):
    """The orb's window (overlay.py), as the controls use it."""

    def show(self) -> None: ...  # main thread
    def hide(self) -> None: ...  # main thread
    def update(self, **fields: Any) -> None: ...  # any thread
    async def apply(self, command: DisplayCommand) -> str: ...  # any thread
    def show_event(self, event: Event) -> None: ...  # any thread


class Controls:
    """Jarvis, as the menu bar sees him: off, starting, on or stopping."""

    def __init__(
        self,
        settings: Settings,
        orb: Window,
        line: Line,
        on_main: Callable[[Callable[[], None]], None],
        crew: Callable[[Settings], Crew] = Crew,
        client: Callable[..., RoomClient] = RoomClient,
    ) -> None:
        self.settings = settings
        self.orb = orb
        self.line = line
        self.on_main = on_main  # runs a function on the main thread
        self.make_crew, self.make_client = crew, client
        self.state: State = "off"
        self.problem: str | None = None  # why he stopped by himself, if he did
        self.muted = False
        self.client: RoomClient | None = None  # the room with Jarvis, while he's on
        self.crew: Crew | None = None  # the server and the agent, while he's on
        self.running: asyncio.Task[None] | None = None  # one Start → Stop
        self.signing_in: set[Service] = set()
        self.background: set[asyncio.Task[Any]] = set()
        self.watchers: list[Callable[[], None]] = []  # told on the main thread after a change
        self.memory = settings.data_dir / MEMORY
        self.remembered = Remembered.load(self.memory)
        self.pressed: dict[str, float] = {}  # when each shortcut last did something
        self.exit_code = 0

    # --- From the main thread ---
    def start(self) -> None:
        self.line.call(self.begin)

    def stop(self) -> None:
        self.line.call(self.end)

    def quit(self, exit_code: int = 0) -> Any:
        """Stop Jarvis (if he's on), then close the app. Returns a future for when it's done."""
        self.exit_code = exit_code
        return self.line.submit(self.shut_down())

    def talk(self) -> None:
        """The talk shortcut: start Jarvis if he's off; else he listens to you now (he wakes,
        or stops talking)."""
        if self.once("talk"):
            self.line.call(self.call_him)

    def toggle_mute(self) -> None:
        if self.once("mute"):
            self.line.call(self.mute, not self.muted)

    def sign_in(self, service: Service) -> None:
        self.line.submit(self.sign_in_to(service))

    def sign_out(self, service: Service) -> None:
        self.line.submit(self.sign_out_of(service))

    def sign_in_clicked(self, service: str) -> None:
        """A sign-in card's button. With Jarvis on, he runs the sign-in, then carries on with
        what you asked; with him off, the desktop app runs it."""
        client = self.client
        if client is not None and client.jarvis is not None:
            client.sign_in_clicked(service)
        else:
            self.sign_in(Service(service))

    def set_start_automatically(self, on: bool) -> None:
        """Start Jarvis as soon as the app opens (with Open at Login: at login)."""
        self.remembered.start_automatically = on
        self.remembered.save(self.memory)
        self.changed()

    def kill(self) -> None:
        """Kill the crew at once: a second Ctrl+C, no tidying up. Any thread."""
        if self.crew is not None:
            self.crew.kill()

    def once(self, key: str) -> bool:
        """False for a second press within `BOUNCE_SECONDS` (one press, delivered twice)."""
        now = time.monotonic()
        if now - self.pressed.get(key, -BOUNCE_SECONDS) < BOUNCE_SECONDS:
            return False
        self.pressed[key] = now
        return True

    # --- On the line's loop ---
    def begin(self) -> None:
        if self.state == "off":
            self.running = asyncio.create_task(self.run())

    def end(self) -> None:
        if self.state in ("starting", "on") and self.running is not None:
            self.running.cancel()

    async def run(self) -> None:
        """One Start → Stop: the crew, then the room with Jarvis, until you stop him or a part
        stops by itself."""
        crew = self.crew = self.make_crew(self.settings)
        room: asyncio.Task[None] | None = None
        stopped: asyncio.Task[Any] | None = None
        self.problem, self.muted = None, False
        self.set_state("starting")
        say("Starting Jarvis…")
        try:
            await crew.start()
            self.on_main(self.orb.show)
            self.client = self.make_client(self.settings, orb=self.orb)
            room = asyncio.create_task(self.client.run())
            stopped = asyncio.create_task(crew.first_to_stop())
            self.set_state("on")
            say("All up. Say “Hey Jarvis”.")
            await asyncio.wait({room, stopped}, return_when=asyncio.FIRST_COMPLETED)
            self.problem = why_he_stopped(room, stopped)
        except PartFailed as failure:
            self.problem = str(failure)
        except asyncio.CancelledError:  # you stopped him: carry on below, then finish normally
            if task := asyncio.current_task():
                task.uncancel()
        finally:
            self.set_state("stopping")
            say("Stopping Jarvis: a few seconds…")
            await self.wind_down(crew, room, stopped)
            self.set_state("off")
            say(f"Jarvis stopped: {self.problem}" if self.problem else "Jarvis is off.")

    async def wind_down(
        self, crew: Crew, room: asyncio.Task[None] | None, stopped: asyncio.Task[Any] | None
    ) -> None:
        """Leave the room (his session ends), hide the orb, then stop the crew in reverse."""
        tasks = [task for task in (room, stopped) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.client = None
        self.on_main(self.orb.hide)
        await crew.stop()

    async def shut_down(self) -> None:
        self.end()
        if self.running is not None:
            await asyncio.gather(self.running, return_exceptions=True)

    def call_him(self) -> None:
        if self.state == "off":
            self.begin()
        elif self.state == "on" and self.client is not None:
            if self.muted:
                self.mute(False)  # calling him means you want to be heard
            self.in_background(self.client.talk())

    def mute(self, muted: bool) -> None:
        if self.state != "on" or self.client is None:
            return
        self.muted = muted
        self.client.mute(muted)
        logger.info("Your mic is %s.", "muted: Jarvis can't hear you" if muted else "on again")
        self.changed()

    async def sign_in_to(self, service: Service) -> None:
        """Open the service's sign-in page in your browser and wait for you; a card beside the
        orb shows how it's going."""
        if service in self.signing_in:
            return
        self.signing_in.add(service)
        self.changed()
        self.orb.show_event(Event("signing_in", {"service": service.value}))
        try:
            await sign_in(self.settings, service)
        except Exception as error:
            logger.warning("The %s sign-in didn't finish: %s", ACCOUNTS[service].name, error)
            self.orb.show_event(Event("sign_in_failed", {"service": service.value}))
        else:
            logger.info("Signed in to %s.", ACCOUNTS[service].name)
            self.orb.show_event(Event("signed_in", {"service": service.value}))
        finally:
            self.signing_in.discard(service)
            self.changed()

    async def sign_out_of(self, service: Service) -> None:
        """Forget the sign-in and ask the service to cancel Jarvis's access."""
        sign_out = google.sign_out if service == Service.google else notion.sign_out
        if await asyncio.to_thread(sign_out, self.settings):
            logger.info("Signed out of %s.", ACCOUNTS[service].name)
        self.changed()

    def set_state(self, state: State) -> None:
        self.state = state
        self.changed()

    def changed(self) -> None:
        """Tell the watchers (the menu bar), on the main thread."""
        for watcher in self.watchers:
            self.on_main(watcher)

    def in_background(self, work: Any) -> None:
        task = asyncio.create_task(work)
        self.background.add(task)  # kept until done: asyncio holds tasks only weakly
        task.add_done_callback(self.background.discard)


def why_he_stopped(room: asyncio.Task[None], stopped: asyncio.Task[Any]) -> str:
    """In words, for the menu and the log."""
    if stopped.done():
        part = stopped.result()
        return f"the {part.part.name} stopped by itself (exit code {part.process.returncode})."
    error = room.exception()
    return f"the line to Jarvis broke ({error})." if error else "the line to Jarvis ended."
