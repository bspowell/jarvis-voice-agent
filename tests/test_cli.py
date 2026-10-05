import pytest
from typer.testing import CliRunner

from jarvis import cli
from jarvis.agents.registry import Health
from jarvis.cli import app
from jarvis.config import Settings
from jarvis.events import Event

runner = CliRunner()


@pytest.fixture(autouse=True)
def offline_roll_call(monkeypatch):
    """`doctor` checks the sub-agents; answer for them so tests never touch the network."""

    async def roll_call(settings):
        return {"search": Health(True, "Firecrawl ready, 480 credits left.")}

    monkeypatch.setattr(cli, "roll_call", roll_call)


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


def test_doctor_shows_the_google_sign_in():
    result = runner.invoke(app, ["doctor"])
    assert "Sign-ins" in result.output
    assert "Google" in result.output


def test_the_sign_in_button_can_be_skipped(monkeypatch):
    monkeypatch.setattr(cli.console, "input", lambda prompt: "skip")
    monkeypatch.setattr(cli.google, "sign_in", lambda settings: pytest.fail("signed in anyway"))
    assert cli.sign_in_button(Settings(_env_file=None)) is False


def test_enter_on_the_sign_in_button_signs_in(monkeypatch):
    signed_in = []
    monkeypatch.setattr(cli.console, "input", lambda prompt: "")
    monkeypatch.setattr(cli.google, "sign_in", signed_in.append)
    settings = Settings(_env_file=None)
    assert cli.sign_in_button(settings) is True
    assert signed_in == [settings]


def test_sign_in_requests_are_shown_once():
    requests = cli.SignInRequests()
    requests.note(Event("sign_in_needed", {"service": "google"}))
    requests.note(Event("agent_started", {"agent": "calendar"}))
    assert requests.take() == {"google"}
    assert requests.take() == set()
