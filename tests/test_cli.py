import pytest
from typer.testing import CliRunner

from jarvis import cli
from jarvis.agents.registry import Health
from jarvis.cli import app

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
