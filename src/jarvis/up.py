"""`jarvis up`: everything voice needs, in one terminal, each part's output tagged.

1. server   the local LiveKit server (`livekit-server --dev`), unless one is running already
2. agent    the voice agent: LiveKit's worker running agent.py
3. desktop  the desktop app: your mic and speakers

Each starts once the one before is ready. Ctrl+C stops them in reverse order: the desktop leaves
(his session ends), then the agent, then the server. If one stops by itself, so does the rest.
Closing the terminal window, or `kill`, stops them the same way. The output is also saved, in
data/logs/up.log (started afresh each run), with the time of every line.
"""

import asyncio
import contextlib
import os
import shutil
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import TextIO
from urllib.parse import urlparse

import httpx
from rich.console import Console
from rich.text import Text

from jarvis.config import PROJECT_ROOT, Settings
from jarvis.voice.health import check_livekit

console = Console(highlight=False, soft_wrap=True)
saved: TextIO | None = None  # the log file, while `jarvis up` runs
READY_SECONDS = 60.0  # a part that isn't ready by then has failed
STOP_SECONDS = 15.0  # a part that hasn't stopped by then is killed
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# `kill`, or the terminal window closing. The parts run in sessions of their own (so Ctrl+C reaches
# only us), so they'd never hear these: we stop them, as for Ctrl+C.
QUIT_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


@dataclass(frozen=True)
class Part:
    """One of the processes `jarvis up` runs."""

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
DESKTOP = Part("desktop", "green", [sys.executable, "-m", "jarvis", "desktop"])


class PartFailed(Exception):
    """A part couldn't start, or stopped by itself."""


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
                kill(self.process)
                await self.process.wait()
        with contextlib.suppress(TimeoutError):  # its last lines (child processes may linger)
            await asyncio.wait_for(self.output, 2)


async def run_all(settings: Settings, started: list[Running]) -> int:
    """Start the parts in order, then wait until one stops (or Ctrl+C). The exit code."""
    env = os.environ | livekit_env(settings)
    stop_on_quit_signals(asyncio.current_task())
    try:
        for part in await parts_to_start(settings):
            process = await asyncio.create_subprocess_exec(
                *part.command,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # Ctrl+C reaches only us: we stop the parts in order
                limit=2**20,  # long log lines (tracebacks)
            )
            started.append(running := Running(part, process))
            await running.until_ready()
        say("All up. Say “Hey Jarvis”. Ctrl+C stops everything.")
        stopped = await first_to_stop(started)
        say(f"The {stopped.part.name} stopped (exit code {stopped.process.returncode}).")
        return 1
    except PartFailed as failure:
        say(str(failure))
        return 1
    finally:
        if any(running.process.returncode is None for running in started):
            say("Stopping everything: a few seconds (Ctrl+C again to force it)…")
        for running in reversed(started):
            await running.stop()
        for quit_signal in QUIT_SIGNALS:
            asyncio.get_running_loop().remove_signal_handler(quit_signal)


def stop_on_quit_signals(task: asyncio.Task | None) -> None:
    """On the first quit signal, stop as for Ctrl+C (`task` is cancelled); ignore the rest."""
    assert task is not None
    loop = asyncio.get_running_loop()

    def quit() -> None:
        for quit_signal in QUIT_SIGNALS:
            loop.add_signal_handler(quit_signal, lambda: None)  # already stopping
        task.cancel()

    for quit_signal in QUIT_SIGNALS:
        loop.add_signal_handler(quit_signal, quit)


async def parts_to_start(settings: Settings) -> list[Part]:
    """The agent and the desktop, and the local server unless it's already answering."""
    if await server_answers(settings):
        say(f"Using the LiveKit server already running at {settings.livekit_url}.")
        return [AGENT, DESKTOP]
    if urlparse(settings.livekit_url).hostname not in LOCAL_HOSTS:
        raise PartFailed(f"The LiveKit server at {settings.livekit_url} isn't answering.")
    if shutil.which("livekit-server") is None:
        raise PartFailed("The LiveKit server isn't installed: `brew install livekit`.")
    return [SERVER, AGENT, DESKTOP]


async def server_answers(settings: Settings) -> bool:
    """Is the LiveKit server answering?"""
    async with httpx.AsyncClient(timeout=1.0) as http:
        try:
            await check_livekit(settings, http)
        except RuntimeError:
            return False
    return True


async def first_to_stop(started: list[Running]) -> Running:
    waits = {asyncio.ensure_future(running.process.wait()): running for running in started}
    try:
        done, _ = await asyncio.wait(set(waits), return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
    return waits[done.pop()]


def livekit_env(settings: Settings) -> dict[str, str]:
    """Where the agent finds the server (LiveKit reads these), passed down from settings."""
    return {
        "LIVEKIT_URL": settings.livekit_url,
        "LIVEKIT_API_KEY": settings.livekit_api_key,
        "LIVEKIT_API_SECRET": settings.livekit_api_secret.get_secret_value(),
    }


def kill(process: asyncio.subprocess.Process) -> None:
    """Kill a part and the processes it started (its own process group)."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def show(name: str, colour: str, line: Text) -> None:
    with contextlib.suppress(OSError):  # the terminal may be gone (its window closed)
        console.print(Text(f"{name:>7} │ ", style=colour), line, sep="")
    if saved is not None:
        saved.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" {name:>7} │ {line.plain}\n")
        saved.flush()


def say(message: str) -> None:
    show("up", "bold cyan", Text(message))


def run_up(settings: Settings) -> int:
    """`jarvis up`. A second Ctrl+C stops waiting and kills whatever is still running."""
    global saved
    log = settings.data_dir / "logs" / "up.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    started: list[Running] = []
    with log.open("w") as saved:
        try:
            return asyncio.run(run_all(settings, started))
        except (KeyboardInterrupt, asyncio.CancelledError):  # Ctrl+C, `kill`, the window closing
            for running in started:
                if running.process.returncode is None:  # only after a second Ctrl+C
                    kill(running.process)
            return 130
        finally:
            saved = None
