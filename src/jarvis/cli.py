"""The `jarvis` command: `uv run jarvis <command>`."""

import typer
from rich.console import Console
from rich.table import Table

from jarvis.config import SECRET_PURPOSES, get_settings

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Jarvis, your dry-witted AI butler."""


@app.command()
def doctor() -> None:
    """Show which API keys and models Jarvis can see (key values stay hidden)."""
    settings = get_settings()

    keys = Table(title="API keys (.env)")
    keys.add_column("Setting")
    keys.add_column("Unlocks")
    keys.add_column("Status")
    for name, purpose in SECRET_PURPOSES.items():
        status = "[green]set[/]" if getattr(settings, name) else "[red]missing[/]"
        keys.add_row(name.upper(), purpose, status)
    console.print(keys)

    models = Table(title="Models (jarvis.toml)")
    models.add_column("Agent")
    models.add_column("Model")
    for agent, model in settings.models.model_dump().items():
        models.add_row(agent, model)
    console.print(models)
