import asyncio

import pytest
from typer.testing import CliRunner

from jarvis import cli
from jarvis.agents.registry import Health
from jarvis.auth.store import SignInFailed
from jarvis.cli import Service, app
from jarvis.config import Settings
from jarvis.events import Event

runner = CliRunner()


@pytest.fixture(autouse=True)
def offline_roll_call(monkeypatch):
    """`doctor` checks the sub-agents and the voice; answer for them so tests stay offline."""

    async def roll_call(settings):
        return {"search": Health(True, "Firecrawl ready, 480 credits left.")}

    async def check_voice(settings):
        return {"Ears (speech to text)": Health(False, "DEEPGRAM_API_KEY is missing from .env.")}

    monkeypatch.setattr(cli, "roll_call", roll_call)
    monkeypatch.setattr(cli, "check_voice", check_voice)


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output


def test_doctor_reports_key_status():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_doctor_reports_sub_agent_health():
    result = runner.invoke(app, ["doctor"])
    assert "present" in result.output
    assert "480 credits left" in result.output


def test_doctor_shows_the_voice_checks():
    result = runner.invoke(app, ["doctor"])
    assert "Voice" in result.output
    assert "not ready" in result.output
    assert "DEEPGRAM_API_KEY is missing" in result.output


def test_doctor_shows_the_sign_ins():
    result = runner.invoke(app, ["doctor"])
    assert "Sign-ins" in result.output
    assert "Google" in result.output
    assert "Notion" in result.output


def test_the_sign_in_button_can_be_skipped(monkeypatch):
    monkeypatch.setattr(cli.console, "input", lambda prompt: "skip")
    monkeypatch.setattr(cli.google, "sign_in", lambda settings: pytest.fail("signed in anyway"))
    with asyncio.Runner() as loop:
        assert cli.sign_in_button(loop, Settings(_env_file=None), Service.google) is False


def test_enter_on_the_sign_in_button_signs_in(monkeypatch):
    signed_in = []
    monkeypatch.setattr(cli.console, "input", lambda prompt: "")
    monkeypatch.setattr(cli.google, "sign_in", signed_in.append)
    settings = Settings(_env_file=None)
    with asyncio.Runner() as loop:
        assert cli.sign_in_button(loop, settings, Service.google) is True
    assert signed_in == [settings]


def test_connect_notion_runs_notion_sign_in_on_the_chat_loop(monkeypatch):
    buttons, signed_in = [], []
    monkeypatch.setattr(cli.console, "input", lambda prompt: buttons.append(prompt) or "")

    async def sign_in(settings):
        signed_in.append(settings)

    monkeypatch.setattr(cli.notion, "sign_in", sign_in)
    settings = Settings(_env_file=None)
    with asyncio.Runner() as loop:
        assert cli.sign_in_button(loop, settings, Service.notion) is True
    assert "Connect Notion" in buttons[0]
    assert signed_in == [settings]


def test_a_failed_sign_in_says_why(monkeypatch):
    monkeypatch.setattr(cli.console, "input", lambda prompt: "")

    async def sign_in(settings):
        raise SignInFailed("Notion said: Access was denied by the authorization server.")

    monkeypatch.setattr(cli.notion, "sign_in", sign_in)
    with asyncio.Runner() as loop:
        assert cli.sign_in_button(loop, Settings(_env_file=None), Service.notion) is False


def test_sign_in_requests_are_shown_once():
    requests = cli.SignInRequests()
    requests.note(Event("sign_in_needed", {"service": "google"}))
    requests.note(Event("agent_started", {"agent": "calendar"}))
    assert requests.take() == {"google"}
    assert requests.take() == set()


def test_the_exact_change_is_printed_under_the_question():
    change = "Move the Notion pages “Games” and “Health [old]” under “Personal”."
    with cli.console.capture() as captured:
        cli.show_event(Event("approval_needed", {"changes": [change]}))
    assert f"↳ {change}" in captured.get()  # brackets survive: printed as text, not markup
