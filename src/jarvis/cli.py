"""The `jarvis` command: `uv run jarvis <command>`."""

import asyncio
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from jarvis.agents.registry import Health, roll_call
from jarvis.auth import google
from jarvis.brain.approvals import Approvals
from jarvis.brain.brain import Brain
from jarvis.brain.greeting import boot_greeting
from jarvis.brain.jarvis import build_jarvis
from jarvis.brain.memory import DB_FILE, open_memory
from jarvis.brain.models import make_model
from jarvis.config import SECRET_PURPOSES, Settings, get_settings
from jarvis.events import Event

app = typer.Typer(no_args_is_help=True)
console = Console()

EXIT_WORDS = {"exit", "quit"}
FILLER = "Right away, sir."  # shown while a sub-agent works; voice will say it (Phase 7)
SIGNED_IN = "I've signed in to Google."  # sent for you after signing in, so Jarvis carries on


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
            approvals = Approvals(make_model(settings.models.approvals))
            brain = Brain(jarvis, new_day_hour=settings.memory.new_day_hour, approvals=approvals)
            sign_ins = SignInRequests()
            brain.events.subscribe(show_event)
            brain.events.subscribe(sign_ins.note)
            with console.status("Calling the roll…"):
                roll = runner.run(roll_call(settings))
            present = sum(health.present for health in roll.values())
            console.print(f"[bold cyan]Jarvis:[/] {boot_greeting(datetime.now(), present)}")
            show_absent_agents(roll)
            if earlier := runner.run(brain.messages_today()):
                console.print(f"[dim](Continuing today's conversation: {earlier} messages.)[/]")
            _chat_loop(runner, brain, settings, sign_ins)
        finally:
            runner.run(memory.close())
    console.print("\n[bold cyan]Jarvis:[/] Very good, sir.")


def show_event(event: Event) -> None:
    """What text chat shows while Jarvis works."""
    if event.name == "agent_started":
        console.print(f"[dim]{FILLER}[/] ", end="")


class SignInRequests:
    """Remembers which sign-in buttons to show once Jarvis has finished speaking."""

    def __init__(self) -> None:
        self.services: set[str] = set()

    def note(self, event: Event) -> None:
        if event.name == "sign_in_needed":
            self.services.add(event.details["service"])

    def take(self) -> set[str]:
        services, self.services = self.services, set()
        return services


def show_absent_agents(roll: dict[str, Health]) -> None:
    for name, health in roll.items():
        if not health.present:
            console.print(f"[dim]({name} agent absent: {health.detail})[/]")


def _chat_loop(
    runner: asyncio.Runner, brain: Brain, settings: Settings, sign_ins: SignInRequests
) -> None:
    while True:
        try:
            text = console.input("\n[bold]You:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if text.lower() in EXIT_WORDS:
            return
        if not text:
            continue
        _reply(runner, brain, text)
        if "google" in sign_ins.take() and sign_in_button(settings):
            console.print(f"\n[bold]You:[/] {SIGNED_IN}")
            _reply(runner, brain, SIGNED_IN)  # Jarvis picks up your original request


def _reply(runner: asyncio.Runner, brain: Brain, text: str) -> None:
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


def sign_in_button(settings: Settings) -> bool:
    """The chat's "Sign in with Google" button: Enter opens the browser. True once signed in."""
    try:
        skip = console.input(
            "[bold cyan]▸ Sign in with Google[/] [dim](press Enter, or type 'skip')[/] "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return not skip and _sign_in(settings)


def _sign_in(settings: Settings) -> bool:
    try:
        with console.status(
            "Waiting for you to approve Jarvis in your browser… (Ctrl+C to cancel)"
        ):
            google.sign_in(settings)
    except google.SignInFailed as error:
        console.print(f"[red]Not signed in:[/] {error}")
        return False
    except KeyboardInterrupt:
        console.print("[dim](Sign-in cancelled.)[/]")
        return False
    console.print("[green]✓ Signed in to Google.[/] Saved in your Keychain.")
    return True


@app.command()
def login() -> None:
    """Sign in to Google now (Jarvis also offers when you first ask about your calendar)."""
    settings = get_settings()
    if not google.is_set_up(settings):
        console.print(f"[red]{google.NOT_SET_UP}[/]")
        raise typer.Exit(1)
    if not _sign_in(settings):
        raise typer.Exit(1)


@app.command()
def logout() -> None:
    """Sign out of Google: Jarvis forgets the sign-in and Google cancels his access."""
    signed_out = google.sign_out(get_settings())
    console.print("Signed out of Google." if signed_out else "Not signed in to Google.")


@app.command()
def doctor() -> None:
    """Show which API keys, sign-ins and models Jarvis can see (key values stay hidden)."""
    settings = get_settings()

    keys = Table(title="API keys (.env)")
    keys.add_column("Setting")
    keys.add_column("Unlocks")
    keys.add_column("Status")
    for name, purpose in SECRET_PURPOSES.items():
        status = "[green]set[/]" if getattr(settings, name) else "[red]missing[/]"
        keys.add_row(name.upper(), purpose, status)
    console.print(keys)

    sign_ins = Table(title="Sign-ins (macOS Keychain)")
    sign_ins.add_column("Account")
    sign_ins.add_column("Status")
    sign_ins.add_row("Google", google_status(settings))
    console.print(sign_ins)

    models = Table(title="Models")
    models.add_column("Agent")
    models.add_column("Model")
    for agent, model in settings.models.model_dump().items():
        models.add_row(agent, model)
    console.print(models)

    agents = Table(title="Sub-agents (health checks)")
    agents.add_column("Agent")
    agents.add_column("Status")
    agents.add_column("Details")
    for name, health in asyncio.run(roll_call(settings)).items():
        status = "[green]present[/]" if health.present else "[red]absent[/]"
        agents.add_row(name, status, health.detail)
    console.print(agents)


def google_status(settings: Settings) -> str:
    if not google.is_set_up(settings):
        return "[dim]not set up: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env[/]"
    store = google.google_store(settings)
    saved = store.saved()
    if saved is None:
        return "[yellow]not signed in[/]: ask about your calendar, or run `jarvis login`"
    renew = store.renew_by(saved).astimezone()
    if store.expired(saved):
        return f"[yellow]needs a fresh sign-in[/] (30 days were up on {renew:%-d %b})"
    return f"[green]signed in[/], until {renew:%-d %b %Y}"
