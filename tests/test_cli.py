from typer.testing import CliRunner

from jarvis.cli import app

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output


def test_doctor_reports_key_status():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY" in result.output
