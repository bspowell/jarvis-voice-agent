"""The `jarvis` command: `uv run jarvis <command>`."""

import asyncio
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from jarvis.brain.brain import Brain
from jarvis.brain.greeting import boot_greeting
from jarvis.brain.jarvis import build_jarvis
from jarvis.brain.memory import DB_FILE, open_memory
from jarvis.config import SECRET_PURPOSES, get_settings

app = typer.Typer(no_args_is_help=True)
console = Console()

EXIT_WORDS = {"exit", "quit"}


@app.callback()
def main() -> None:
    """Jarvis, your dry-witted AI butler."""


@app.command()
def chat() -> None:
    """Chat with Jarvis in the terminal. Type 'exit' (or press Ctrl+C) to leave."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        console.print(
            "[red]ANTHROPIC_API_KEY is missing.[/] Add it to .env, then run `jarvis doctor`."
        )
        raise typer.Exit(1)

    # One event loop for the whole chat, so the memory's database connections stay valid.
    with asyncio.Runner() as runner:
        memory = runner.run(open_memory(settings.data_dir / DB_FILE))
        try:
            jarvis = build_jarvis(settings, checkpointer=memory.checkpointer, store=memory.store)
            brain = Brain(jarvis, new_day_hour=settings.memory.new_day_hour)
            greeting = boot_greeting(datetime.now(), agents_present=0)
            console.print(f"[bold cyan]Jarvis:[/] {greeting}")
            if earlier := runner.run(brain.messages_today()):
                console.print(f"[dim](Continuing today's conversation: {earlier} messages.)[/]")
            _chat_loop(runner, brain)
        finally:
            runner.run(memory.close())
    console.print("\n[bold cyan]Jarvis:[/] Very good, sir.")


def _chat_loop(runner: asyncio.Runner, brain: Brain) -> None:
    while True:
        try:
            text = console.input("\n[bold]You:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if text.lower() in EXIT_WORDS:
            return
        if not text:
            continue
        try:
            runner.run(_print_reply(brain, text))
        except KeyboardInterrupt:
            console.print("\n[dim](interrupted)[/]")


async def _print_reply(brain: Brain, text: str) -> None:
    console.print("[bold cyan]Jarvis:[/] ", end="")
    try:
        async for chunk in brain.turn(text):
            console.out(chunk, end="", highlight=False)
    except Exception as error:  # keep the chat alive and show what went wrong
        console.print(f"\n[red]Something went wrong ({type(error).__name__}): {error}[/]")
    console.print()


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

    models = Table(title="Models")
    models.add_column("Agent")
    models.add_column("Model")
    for agent, model in settings.models.model_dump().items():
        models.add_row(agent, model)
    console.print(models)
