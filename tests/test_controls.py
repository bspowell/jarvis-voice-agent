"""What the menu bar and the shortcuts do: start and stop Jarvis, mute, talk, sign in. With
stand-ins for the crew (the server and the agent) and the room."""

import asyncio
import time

import pytest

from jarvis.auth.accounts import Service
from jarvis.config import Settings
from jarvis.desktop import controls as controls_module
from jarvis.desktop.controls import Controls, Remembered
from jarvis.events import Event
from jarvis.up import PartFailed

done: list[str] = []  # what happened, in order, across the stand-ins


class FakeCrew:
    def __init__(self, settings, fail: str | None = None, slow: bool = False) -> None:
        self.fail, self.slow = fail, slow
        self.part_stopped = asyncio.Event()  # set it: a part stops by itself
        self.killed = False

    async def start(self) -> None:
        done.append("crew started")
        if self.slow:
            await asyncio.sleep(3600)
        if self.fail:
            raise PartFailed(self.fail)

    async def first_to_stop(self):
        await self.part_stopped.wait()

        class Stopped:  # how `Running` describes a part
            class part:
                name = "agent"

            class process:
                returncode = 1

        return Stopped

    async def stop(self) -> None:
        done.append("crew stopped")

    def kill(self) -> None:
        self.killed = True


class FakeClient:
    def __init__(self, settings, orb) -> None:
        self.jarvis: str | None = "agent-AJ_1"
        self.muted: list[bool] = []
        self.talks = 0
        self.clicks: list[str] = []

    async def run(self) -> None:
        done.append("room joined")
        try:
            await asyncio.sleep(3600)
        finally:
            done.append("room left")

    def mute(self, muted: bool) -> None:
        self.muted.append(muted)

    async def talk(self) -> None:
        self.talks += 1

    def sign_in_clicked(self, service: str) -> None:
        self.clicks.append(service)


class FakeOrb:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def show(self) -> None:
        done.append("orb shown")

    def hide(self) -> None:
        done.append("orb hidden")

    def update(self, **fields) -> None:
        pass

    async def apply(self, command) -> str:
        return ""

    def show_event(self, event: Event) -> None:
        self.events.append(event)


class FakeLine:
    """The line, on the test's own loop."""

    def call(self, work, *args) -> None:
        asyncio.get_running_loop().call_soon(work, *args)

    def submit(self, work):
        return asyncio.ensure_future(work)


@pytest.fixture(autouse=True)
def fresh():
    done.clear()


def make(tmp_path, **crew_options) -> Controls:
    crews: list[FakeCrew] = []

    def crew(settings):
        crews.append(made := FakeCrew(settings, **crew_options))
        return made

    controls = Controls(
        Settings(_env_file=None, data_dir=tmp_path),
        FakeOrb(),
        FakeLine(),
        on_main=lambda work: work(),
        crew=crew,
        client=FakeClient,
    )
    controls.crews = crews  # for the tests
    return controls


async def until(condition, seconds: float = 2) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def test_start_then_stop(tmp_path):
    controls = make(tmp_path)
    states: list[str] = []
    controls.watchers.append(lambda: states.append(controls.state))
    controls.start()
    await until(lambda: controls.state == "on" and "room joined" in done)
    controls.stop()
    await until(lambda: controls.state == "off")
    assert done == [
        *("crew started", "orb shown", "room joined"),  # the room once the crew is ready
        *("room left", "orb hidden", "crew stopped"),  # he leaves first, then the crew stops
    ]
    assert states == ["starting", "on", "stopping", "off"]  # each change reaches the menu
    assert controls.problem is None


async def test_a_part_that_fails_to_start_leaves_him_off_and_says_why(tmp_path):
    controls = make(tmp_path, fail="The agent didn't start (exit code 3).")
    controls.start()
    await until(lambda: "crew stopped" in done)
    await until(lambda: controls.state == "off")
    assert "orb shown" not in done
    assert controls.problem == "The agent didn't start (exit code 3)."


async def test_a_part_stopping_by_itself_stops_him(tmp_path):
    controls = make(tmp_path)
    controls.start()
    await until(lambda: "room joined" in done)
    controls.crews[0].part_stopped.set()
    await until(lambda: controls.state == "off")
    assert done[-3:] == ["room left", "orb hidden", "crew stopped"]
    assert controls.problem == "the agent stopped by itself (exit code 1)."


async def test_stop_while_starting_stops_whatever_started(tmp_path):
    controls = make(tmp_path, slow=True)
    controls.start()
    await until(lambda: "crew started" in done)
    assert controls.state == "starting"
    controls.stop()
    await until(lambda: controls.state == "off")
    assert done == ["crew started", "orb hidden", "crew stopped"]
    assert controls.problem is None  # you stopped him: nothing went wrong


async def test_quit_stops_him_first(tmp_path):
    controls = make(tmp_path)
    controls.start()
    await until(lambda: "room joined" in done)
    await controls.quit(exit_code=130)
    assert controls.state == "off"
    assert done[-1] == "crew stopped"
    assert controls.exit_code == 130


async def test_the_talk_shortcut_starts_him_then_calls_him(tmp_path):
    controls = make(tmp_path)
    controls.talk()  # off: starts him
    await until(lambda: controls.state == "on" and controls.client is not None)
    controls.pressed.clear()  # the next press is a new one
    controls.mute(True)
    controls.talk()  # on and muted: unmuted, then called
    await until(lambda: controls.client.talks == 1)
    assert controls.client.muted == [True, False]
    assert controls.muted is False
    controls.stop()
    await until(lambda: controls.state == "off")


async def test_mute_works_only_while_he_is_on_and_counts_one_press_once(tmp_path):
    controls = make(tmp_path)
    controls.toggle_mute()  # off: nothing to mute
    await asyncio.sleep(0.01)
    assert controls.muted is False
    controls.pressed.clear()
    controls.start()
    await until(lambda: controls.state == "on" and controls.client is not None)
    controls.toggle_mute()
    controls.toggle_mute()  # the same press, arriving twice (shortcut + open menu)
    await until(lambda: controls.client.muted == [True])
    await asyncio.sleep(0.05)
    assert controls.client.muted == [True]
    controls.stop()
    await until(lambda: controls.state == "off")


async def test_a_sign_in_card_goes_to_jarvis_when_he_is_there(tmp_path, monkeypatch):
    controls = make(tmp_path)
    by_desktop: list[Service] = []
    monkeypatch.setattr(controls, "sign_in", by_desktop.append)
    controls.sign_in_clicked("notion")  # he's off: the desktop app signs in
    controls.start()
    await until(lambda: controls.state == "on" and controls.client is not None)
    controls.sign_in_clicked("google")  # he's there: he signs in, then carries on
    assert by_desktop == [Service.notion]
    assert controls.client.clicks == ["google"]
    controls.stop()
    await until(lambda: controls.state == "off")


async def test_signing_in_from_the_menu_shows_its_card(tmp_path, monkeypatch):
    controls = make(tmp_path)
    outcomes = iter([None, RuntimeError("Access was denied")])

    async def sign_in(settings, service):
        if (outcome := next(outcomes)) is not None:
            raise outcome

    monkeypatch.setattr(controls_module, "sign_in", sign_in)
    await controls.sign_in_to(Service.google)
    await controls.sign_in_to(Service.notion)
    assert [(event.name, event.details["service"]) for event in controls.orb.events] == [
        ("signing_in", "google"),
        ("signed_in", "google"),
        ("signing_in", "notion"),
        ("sign_in_failed", "notion"),
    ]
    assert controls.signing_in == set()


def test_start_automatically_is_remembered_and_off_by_default(tmp_path):
    controls = make(tmp_path)
    assert controls.remembered.start_automatically is False
    controls.set_start_automatically(True)
    assert Remembered.load(tmp_path / "app.json").start_automatically is True
    (tmp_path / "app.json").write_text("{damaged")
    assert Remembered.load(tmp_path / "app.json") == Remembered()
