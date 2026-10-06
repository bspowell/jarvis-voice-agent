"""`jarvis up`: start order, tagged output, and stopping, with small stand-in processes
instead of the LiveKit server, the agent and the desktop app."""

import asyncio
import io
import os
import re
import signal
import sys
import time

import pytest
from rich.console import Console

from jarvis import up
from jarvis.config import Settings
from jarvis.up import AGENT, DESKTOP, SERVER, Part, PartFailed

# A stand-in part: notes when it starts, is asked to stop (SIGTERM) or quits by itself.
STAND_IN = """
import pathlib, signal, sys, time
name, log, lifetime = sys.argv[1], pathlib.Path(sys.argv[2]), float(sys.argv[3])
def note(what):
    with log.open("a") as file:
        file.write(f"{name} {what}\\n")
def stop(*_):
    note("stopped")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
note("started")
print("lots of DEBUG detail", flush=True)
print(f"{name} is ready", flush=True)
time.sleep(lifetime)
note("quit by itself")
"""


def stand_in(name: str, log, lifetime: float = 60) -> Part:
    command = [sys.executable, "-c", STAND_IN, name, str(log), str(lifetime)]
    return Part(name, "white", command, ready_line=f"{name} is ready", hidden=("DEBUG",))


@pytest.fixture
def shown(monkeypatch) -> io.StringIO:
    """What `jarvis up` prints."""
    output = io.StringIO()
    monkeypatch.setattr(up, "console", Console(file=output, width=200, soft_wrap=True))
    return output


def run_these(monkeypatch, *parts: Part) -> None:
    async def these(settings):
        return list(parts)

    monkeypatch.setattr(up, "parts_to_start", these)


async def until(condition, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


async def test_parts_start_in_order_and_ctrl_c_stops_them_in_reverse(monkeypatch, tmp_path, shown):
    log = tmp_path / "log"
    run_these(monkeypatch, *(stand_in(name, log) for name in ("server", "agent", "desktop")))
    running = asyncio.create_task(up.run_all(Settings(_env_file=None), []))
    await until(lambda: "All up" in shown.getvalue())
    running.cancel()  # what Ctrl+C does to `asyncio.run`
    with pytest.raises(asyncio.CancelledError):
        await running
    assert log.read_text().splitlines() == [
        *("server started", "agent started", "desktop started"),  # each once the last was ready
        *("desktop stopped", "agent stopped", "server stopped"),  # desktop leaves first
    ]
    assert "  agent │ agent is ready" in shown.getvalue()  # each line tagged with its part
    assert "DEBUG" not in shown.getvalue()  # hidden lines stay hidden


async def test_kill_or_closing_the_terminal_stops_everything_too(monkeypatch, tmp_path, shown):
    log = tmp_path / "log"
    run_these(monkeypatch, stand_in("server", log), stand_in("desktop", log))
    running = asyncio.create_task(up.run_all(Settings(_env_file=None), []))
    await until(lambda: "All up" in shown.getvalue())
    os.kill(os.getpid(), signal.SIGHUP)  # what closing the terminal window sends
    with pytest.raises(asyncio.CancelledError):
        await running
    assert log.read_text().splitlines()[-2:] == ["desktop stopped", "server stopped"]


async def test_when_one_part_stops_the_rest_are_stopped(monkeypatch, tmp_path, shown):
    log = tmp_path / "log"
    run_these(monkeypatch, stand_in("server", log), stand_in("desktop", log, lifetime=0.3))
    assert await up.run_all(Settings(_env_file=None), []) == 1
    assert log.read_text().splitlines()[-2:] == ["desktop quit by itself", "server stopped"]
    assert "The desktop stopped (exit code 0)." in shown.getvalue()


async def test_a_part_that_fails_to_start_stops_everything(monkeypatch, tmp_path, shown):
    log = tmp_path / "log"
    broken = Part("agent", "white", [sys.executable, "-c", "raise SystemExit(3)"], "never")
    run_these(monkeypatch, stand_in("server", log), broken, stand_in("desktop", log))
    assert await up.run_all(Settings(_env_file=None), []) == 1
    assert log.read_text().splitlines() == ["server started", "server stopped"]
    assert "The agent didn't start (exit code 3)." in shown.getvalue()


def test_the_output_is_also_saved_with_the_time_of_each_line(monkeypatch, tmp_path, shown):
    run_these(monkeypatch, stand_in("desktop", tmp_path / "notes", lifetime=0.2))
    assert up.run_up(Settings(_env_file=None, data_dir=tmp_path)) == 1  # it stopped by itself
    saved = (tmp_path / "logs" / "up.log").read_text().splitlines()
    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} desktop │ desktop is ready", saved[0])


async def test_a_server_already_running_is_used(monkeypatch, shown):
    async def answers(settings):
        return True

    monkeypatch.setattr(up, "server_answers", answers)
    assert await up.parts_to_start(Settings(_env_file=None)) == [AGENT, DESKTOP]
    assert "Using the LiveKit server already running" in shown.getvalue()


async def test_a_local_server_is_started_when_none_answers(monkeypatch):
    async def silent(settings):
        return False

    monkeypatch.setattr(up, "server_answers", silent)
    monkeypatch.setattr(up.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    assert await up.parts_to_start(Settings(_env_file=None)) == [SERVER, AGENT, DESKTOP]
    monkeypatch.setattr(up.shutil, "which", lambda name: None)
    with pytest.raises(PartFailed, match="brew install livekit"):
        await up.parts_to_start(Settings(_env_file=None))


async def test_a_remote_server_is_never_started_here(monkeypatch):
    async def silent(settings):
        return False

    monkeypatch.setattr(up, "server_answers", silent)
    remote = Settings(_env_file=None, livekit_url="wss://jarvis.livekit.cloud")
    with pytest.raises(PartFailed, match="isn't answering"):
        await up.parts_to_start(remote)
