"""Jarvis's crew: the processes the desktop app starts when you start Jarvis.

1. server   the local LiveKit server (`livekit-server --dev`), unless one is running already
2. agent    the voice agent: LiveKit's worker running agent.py

The desktop app (desktop/app.py) is their parent. It starts each once the one before is ready,
and stops them in reverse when you stop Jarvis or quit (if one stops by itself, Jarvis stops).
Their output is tagged with their names and saved in data/logs/up.log, with the time of every
line, beside the desktop app's own; in a terminal (`jarvis up`) it shows there too.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

import httpx
from rich.console import Console
from rich.text import Text

from jarvis.config import PROJECT_ROOT, Settings
from jarvis.voice.health import check_livekit

console = Console(highlight=False, soft_wrap=True)
echo = True  # show output in the terminal too (not when macOS starts the app: no terminal)
saved: TextIO | None = None  # the log file, while the desktop app runs
writing = threading.Lock()  # lines come from both of the desktop app's threads
LOG = Path("logs") / "up.log"  # in data/
LEFTOVERS = "crew.json"  # in data/: the crew's process ids while they run
READY_SECONDS = 60.0  # a part that isn't ready by then has failed
STOP_SECONDS = 15.0  # a part that hasn't stopped by then is killed
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Part:
    """One of the processes the crew runs."""

    name: str
    colour: str
    command: list[str]
    ready_line: str = ""  # appears in its output once it's ready ("": ready at once)
    hidden: tuple[str, ...] = ()  # its output lines containing any of these aren't shown


SERVER = Part(
    "server",
    "blue",
    ["livekit-server", "--dev"],
    ready_line="starting LiveKit server",
    hidden=("\tDEBUG\t",),  # --dev logs every detail of every connection
)
AGENT = Part(
    "agent",
    "magenta",
    [
        sys.executable,
        "-m",
        "livekit.agents",
        "start",
        str(PROJECT_ROOT / "agent.py"),
        "--log-format",
        "colored",
    ],
    ready_line="registered worker",
)


class PartFailed(Exception):
    """A part couldn't start."""


class Running:
    """A started part: shows its output as it comes and knows when it's ready."""

    def __init__(self, part: Part, process: asyncio.subprocess.Process) -> None:
        self.part = part
        self.process = process
        self.ready = asyncio.Event()
        if not part.ready_line:
            self.ready.set()
        self.output = asyncio.create_task(self.show_output())

    async def show_output(self) -> None:
        assert self.process.stdout is not None
        async for raw in self.process.stdout:
            line = raw.decode(errors="replace").rstrip()
            if self.part.ready_line and self.part.ready_line in line:
                self.ready.set()
            if not any(hidden in line for hidden in self.part.hidden):
                show(self.part.name, self.part.colour, Text.from_ansi(line))

    async def until_ready(self) -> None:
        ready = asyncio.ensure_future(self.ready.wait())
        stopped = asyncio.ensure_future(self.process.wait())
        try:
            await asyncio.wait(
                {ready, stopped}, timeout=READY_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            ready.cancel()
            stopped.cancel()
        if not self.ready.is_set():
            code = self.process.returncode
            why = f"exit code {code}" if code is not None else f"not ready in {READY_SECONDS:g} s"
            raise PartFailed(f"The {self.part.name} didn't start ({why}).")

    async def stop(self) -> None:
        """Ask the part to stop (each one tidies up first); kill it if it takes too long.

        SIGTERM rather than Ctrl+C's SIGINT: a shell ignores SIGINT for whatever it starts in
        the background, and the parts would inherit that."""
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), STOP_SECONDS)
            except TimeoutError:
                kill(self.process.pid)
                await self.process.wait()
        with contextlib.suppress(TimeoutError):  # its last lines (child processes may linger)
            await asyncio.wait_for(self.output, 2)


class Crew:
    """The server and the agent, while Jarvis is on. Runs on one asyncio loop (the desktop
    app's line). Their process ids are kept in data/crew.json while they run: if the desktop
    app dies without stopping them, its next launch does (`clean_up_leftovers`)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started: list[Running] = []
        self.leftovers = settings.data_dir / LEFTOVERS

    async def start(self) -> None:
        """Start each part once the one before is ready. Raises PartFailed; the parts already
        running keep running, so call `stop` either way."""
        env = os.environ | livekit_env(self.settings)
        for part in await parts_to_start(self.settings):
            process = await asyncio.create_subprocess_exec(
                *part.command,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # Ctrl+C in a terminal reaches only the app: it stops them
                limit=2**20,  # long log lines (tracebacks)
            )
            self.started.append(running := Running(part, process))
            self.note_pids()
            await running.until_ready()

    async def first_to_stop(self) -> Running:
        """Wait until one of the parts stops by itself."""
        waits = {asyncio.ensure_future(r.process.wait()): r for r in self.started}
        try:
            done, _ = await asyncio.wait(set(waits), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for wait in waits:
                wait.cancel()
        return waits[done.pop()]

    async def stop(self) -> None:
        """Stop the parts in reverse order: the agent, then the server."""
        for running in reversed(self.started):
            await running.stop()
        self.started = []
        self.note_pids()

    def kill(self) -> None:
        """Kill the parts at once (a second Ctrl+C: no tidying up)."""
        for running in self.started:
            if running.process.returncode is None:
                kill(running.process.pid)

    def note_pids(self) -> None:
        running = [
            {"pid": r.process.pid, "command": r.part.command[0]}
            for r in self.started
            if r.process.returncode is None
        ]
        if running:
            self.leftovers.parent.mkdir(parents=True, exist_ok=True)
            self.leftovers.write_text(json.dumps(running))
        else:
            self.leftovers.unlink(missing_ok=True)


def clean_up_leftovers(settings: Settings) -> None:
    """Stop any part a previous desktop app left running (it crashed or was killed): otherwise
    an old agent would share the next sessions with the new one."""
    path = settings.data_dir / LEFTOVERS
    try:
        noted = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    for entry in noted if isinstance(noted, list) else []:
        pid, command = entry.get("pid"), entry.get("command")
        if isinstance(pid, int) and isinstance(command, str) and runs(pid, command):
            say(f"Stopping a part left running last time (process {pid}).")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGTERM)
            if not gone_within(pid, STOP_SECONDS):
                kill(pid)
    path.unlink(missing_ok=True)


def runs(pid: int, command: str) -> bool:
    """Is process `pid` still running `command`? (Its number may have gone to another one.)"""
    found = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return found.returncode == 0 and found.stdout.strip().startswith(command)


def gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)  # signal 0: only checks it exists
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


async def parts_to_start(settings: Settings) -> list[Part]:
    """The agent, and the local server unless it's already answering."""
    if await server_answers(settings):
        say(f"Using the LiveKit server already running at {settings.livekit_url}.")
        return [AGENT]
    if urlparse(settings.livekit_url).hostname not in LOCAL_HOSTS:
        raise PartFailed(f"The LiveKit server at {settings.livekit_url} isn't answering.")
    if shutil.which("livekit-server") is None:
        raise PartFailed("The LiveKit server isn't installed: `brew install livekit`.")
    return [SERVER, AGENT]


async def server_answers(settings: Settings) -> bool:
    """Is the LiveKit server answering?"""
    async with httpx.AsyncClient(timeout=1.0) as http:
        try:
            await check_livekit(settings, http)
        except RuntimeError:
            return False
    return True


def livekit_env(settings: Settings) -> dict[str, str]:
    """Where the agent finds the server (LiveKit reads these), passed down from settings."""
    return {
        "LIVEKIT_URL": settings.livekit_url,
        "LIVEKIT_API_KEY": settings.livekit_api_key,
        "LIVEKIT_API_SECRET": settings.livekit_api_secret.get_secret_value(),
    }


def kill(pid: int) -> None:
    """Kill a part and the processes it started (its own process group)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):  # gone (or only a zombie)
        os.killpg(pid, signal.SIGKILL)


# --- The output: tagged lines, in the terminal (if any) and in data/logs/up.log ---
def open_log(settings: Settings, in_terminal: bool) -> None:
    """Start the log afresh (each time the desktop app opens); show lines in the terminal too
    if there is one."""
    global saved, echo
    path = settings.data_dir / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    saved, echo = path.open("w"), in_terminal


def tag_log_messages() -> None:
    """The desktop app's own log messages go to the same places, tagged "desktop"."""
    handler = Tagged("desktop", "green")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def close_log() -> None:
    global saved
    with writing:
        if saved is not None:
            saved.close()
            saved = None


class Tagged(logging.Handler):
    """Log messages as tagged lines, like the parts' output."""

    def __init__(self, name: str, colour: str) -> None:
        super().__init__()
        self.tag, self.colour = name, colour

    def emit(self, record: logging.LogRecord) -> None:
        show(self.tag, self.colour, Text(self.format(record)))


def show(name: str, colour: str, line: Text) -> None:
    with writing:
        if echo:
            with contextlib.suppress(OSError):  # the terminal may be gone (its window closed)
                console.print(Text(f"{name:>7} │ ", style=colour), line, sep="")
        if saved is not None:
            saved.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" {name:>7} │ {line.plain}\n")
            saved.flush()


def say(message: str) -> None:
    show("up", "bold cyan", Text(message))
