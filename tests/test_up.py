"""The crew: start order, tagged output, stopping, and parts left running by a crashed app, with
small stand-in processes instead of the LiveKit server and the agent."""

import asyncio
import io
import json
import re
import subprocess
import sys
import time

import pytest
from rich.console import Console

from jarvis import up
from jarvis.config import Settings
from jarvis.up import AGENT, SERVER, Crew, Part, PartFailed

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
    """What the crew shows in the terminal."""
    output = io.StringIO()
    monkeypatch.setattr(up, "console", Console(file=output, width=200, soft_wrap=True))
    monkeypatch.setattr(up, "echo", True)
    return output


def run_these(monkeypatch, *parts: Part) -> None:
    async def these(settings):
        return list(parts)

    monkeypatch.setattr(up, "parts_to_start", these)


def settings_in(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path)


async def test_parts_start_in_order_and_stop_in_reverse(monkeypatch, tmp_path, shown):
    log = tmp_path / "log"
    run_these(monkeypatch, stand_in("server", log), stand_in("agent", log))
    crew = Crew(settings_in(tmp_path))
    await crew.start()
    await crew.stop()
    assert log.read_text().splitlines() == [
        "server started",  # the agent starts only once the server is ready
        "agent started",
        "agent stopped",  # the agent leaves first, then the server
        "server stopped",
    ]
    assert "  agent │ agent is ready" in shown.getvalue()  # each line tagged with its part
    assert "DEBUG" not in shown.getvalue()  # hidden lines stay hidden


async def test_a_part_that_fails_to_start_says_why(monkeypatch, tmp_path, shown):
    log = tmp_path / "log"
    broken = Part("agent", "white", [sys.executable, "-c", "raise SystemExit(3)"], "never")
    run_these(monkeypatch, stand_in("server", log), broken)
    crew = Crew(settings_in(tmp_path))
    with pytest.raises(PartFailed, match=r"The agent didn't start \(exit code 3\)\."):
        await crew.start()
    await crew.stop()  # the parts already running are stopped
    assert log.read_text().splitlines() == ["server started", "server stopped"]


async def test_the_crew_hears_when_a_part_stops_by_itself(monkeypatch, tmp_path, shown):
    log = tmp_path / "log"
    run_these(monkeypatch, stand_in("server", log), stand_in("agent", log, lifetime=0.3))
    crew = Crew(settings_in(tmp_path))
    await crew.start()
    stopped = await asyncio.wait_for(crew.first_to_stop(), 10)
    assert stopped.part.name == "agent"
    assert stopped.process.returncode == 0
    await crew.stop()
    assert log.read_text().splitlines()[-2:] == ["agent quit by itself", "server stopped"]


async def test_running_parts_are_noted_so_a_crashed_app_can_clean_up(monkeypatch, tmp_path, shown):
    run_these(monkeypatch, stand_in("server", tmp_path / "log"))
    crew = Crew(settings_in(tmp_path))
    await crew.start()
    noted = json.loads((tmp_path / up.LEFTOVERS).read_text())
    assert noted == [{"pid": crew.started[0].process.pid, "command": sys.executable}]
    await crew.stop()
    assert not (tmp_path / up.LEFTOVERS).exists()  # stopped properly: nothing to clean up


# Starts a part the way a crashed app leaves it: in its own session, its parent gone.
ORPHAN = """
import subprocess, sys
quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}  # our pipes: let go
part = subprocess.Popen(sys.argv[1:], start_new_session=True, **quiet)
print(part.pid)
"""


def test_parts_left_running_last_time_are_stopped(tmp_path, shown):
    log = tmp_path / "log"
    started = subprocess.run(
        [sys.executable, "-c", ORPHAN, *stand_in("agent", log).command],
        capture_output=True,
        text=True,
        check=True,
    )
    pid = int(started.stdout)
    deadline = time.monotonic() + 10
    while not log.exists() or "started" not in log.read_text():
        assert time.monotonic() < deadline, "the stand-in never started"
        time.sleep(0.05)
    (tmp_path / up.LEFTOVERS).write_text(json.dumps([{"pid": pid, "command": sys.executable}]))
    up.clean_up_leftovers(settings_in(tmp_path))
    assert log.read_text().splitlines() == ["agent started", "agent stopped"]  # it tidied up
    assert not (tmp_path / up.LEFTOVERS).exists()
    assert "left running last time" in shown.getvalue()


def test_a_process_that_isnt_ours_is_left_alone(tmp_path):
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        noted = [{"pid": sleeper.pid, "command": "livekit-server"}]  # the number, reused
        (tmp_path / up.LEFTOVERS).write_text(json.dumps(noted))
        up.clean_up_leftovers(settings_in(tmp_path))
        assert sleeper.poll() is None  # still running
    finally:
        sleeper.kill()
        sleeper.wait()


def test_the_output_is_saved_with_the_time_of_each_line(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "saved", None)
    monkeypatch.setattr(up, "echo", True)
    up.open_log(settings_in(tmp_path), in_terminal=False)
    try:
        up.say("All up.")
    finally:
        up.close_log()
    saved = (tmp_path / "logs" / "up.log").read_text().splitlines()
    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3}      up │ All up\.", saved[0])


async def test_a_server_already_running_is_used(monkeypatch, shown):
    async def answers(settings):
        return True

    monkeypatch.setattr(up, "server_answers", answers)
    assert await up.parts_to_start(Settings(_env_file=None)) == [AGENT]
    assert "Using the LiveKit server already running" in shown.getvalue()


async def test_a_local_server_is_started_when_none_answers(monkeypatch):
    async def silent(settings):
        return False

    monkeypatch.setattr(up, "server_answers", silent)
    monkeypatch.setattr(up.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    assert await up.parts_to_start(Settings(_env_file=None)) == [SERVER, AGENT]
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
