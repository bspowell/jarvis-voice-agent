"""The `jarvis` command: `uv run jarvis <command>`."""

import asyncio
from datetime import datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from jarvis.agents.registry import Health, roll_call
from jarvis.auth import google, notion
from jarvis.auth.accounts import ACCOUNTS, Service
from jarvis.auth.store import SignInFailed, SignInStore
from jarvis.brain.brain import Brain
from jarvis.brain.greeting import FILLER, boot_greeting
from jarvis.brain.jarvis import make_brain
from jarvis.brain.memory import DB_FILE, open_memory
from jarvis.config import SECRET_PURPOSES, Settings, get_settings
from jarvis.events import Event
from jarvis.voice.health import check_voice

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
            brain = make_brain(settings, memory)
            sign_ins, links = SignInRequests(), FurtherReading()
            brain.events.subscribe(show_event)
            brain.events.subscribe(sign_ins.note)
            brain.events.subscribe(links.note)
            with console.status("Calling the roll…"):
                roll = runner.run(roll_call(settings))
            present = sum(health.present for health in roll.values())
            console.print(f"[bold cyan]Jarvis:[/] {boot_greeting(datetime.now(), present)}")
            show_absent_agents(roll)
            if earlier := runner.run(brain.messages_today()):
                console.print(f"[dim](Continuing today's conversation: {earlier} messages.)[/]")
            _chat_loop(runner, brain, settings, sign_ins, links)
        finally:
            runner.run(memory.close())
    console.print("\n[bold cyan]Jarvis:[/] Very good, sir.")


def show_event(event: Event) -> None:
    """What text chat shows while Jarvis works."""
    if event.name == "agent_started":
        console.print(f"[dim]{FILLER}[/] ", end="")
    elif event.name == "approval_needed":  # under "Shall I…?": exactly what a yes would do
        for change in event.details["changes"]:
            console.print(f"\n[dim]  ↳ {escape(change)}[/]", end="")


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


class FurtherReading:
    """The Search agent's links, printed under Jarvis's reply (the desktop shows them on a card)."""

    def __init__(self) -> None:
        self.links: list[dict[str, str]] = []

    def note(self, event: Event) -> None:
        if event.name == "further_reading":
            self.links = event.details["sources"]

    def show(self) -> None:
        for link in self.links:
            console.print(f"[dim]  ↳ {escape(link['title'])}: {escape(link['url'])}[/]")
        self.links = []


def show_absent_agents(roll: dict[str, Health]) -> None:
    for name, health in roll.items():
        if not health.present:
            console.print(f"[dim]({name} agent absent: {health.detail})[/]")


def _chat_loop(
    runner: asyncio.Runner,
    brain: Brain,
    settings: Settings,
    sign_ins: SignInRequests,
    links: FurtherReading,
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
        links.show()
        for service in sign_ins.take():
            if sign_in_button(runner, settings, Service(service)):
                said = ACCOUNTS[Service(service)].signed_in
                console.print(f"\n[bold]You:[/] {said}")
                _reply(runner, brain, said)  # Jarvis picks up your original request


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


def sign_in_button(runner: asyncio.Runner, settings: Settings, service: Service) -> bool:
    """The chat's sign-in button, e.g. "Connect Notion": Enter opens the browser. True once in."""
    try:
        skip = console.input(
            f"[bold cyan]▸ {ACCOUNTS[service].button}[/] [dim](press Enter, or type 'skip')[/] "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return not skip and _sign_in(runner, settings, service)


def _sign_in(runner: asyncio.Runner, settings: Settings, service: Service) -> bool:
    name = ACCOUNTS[service].name
    try:
        with console.status(
            "Waiting for you to approve Jarvis in your browser… (Ctrl+C to cancel)"
        ):
            if service == Service.google:
                google.sign_in(settings)  # Google's library waits by itself
            else:
                runner.run(notion.sign_in(settings))  # Notion's runs on the chat's event loop
    except SignInFailed as error:
        console.print(f"[red]Not signed in to {name}:[/] {error}")
        return False
    except KeyboardInterrupt:
        console.print("[dim](Sign-in cancelled.)[/]")
        return False
    console.print(f"[green]✓ Signed in to {name}.[/] Saved in your Keychain.")
    return True


ServiceArgument = Annotated[Service, typer.Argument(help="Which account: google or notion.")]


@app.command()
def login(service: ServiceArgument = Service.google) -> None:
    """Sign in now (Jarvis also offers a button the first time he needs the account)."""
    settings = get_settings()
    if service == Service.google and not google.is_set_up(settings):
        console.print(f"[red]{google.NOT_SET_UP}[/]")
        raise typer.Exit(1)
    with asyncio.Runner() as runner:
        if not _sign_in(runner, settings, service):
            raise typer.Exit(1)


@app.command()
def logout(service: ServiceArgument = Service.google) -> None:
    """Sign out: Jarvis forgets the sign-in and asks the service to cancel his access."""
    sign_out = google.sign_out if service == Service.google else notion.sign_out
    name = ACCOUNTS[service].name
    console.print(
        f"Signed out of {name}." if sign_out(get_settings()) else f"Not signed in to {name}."
    )


@app.command()
def desktop(
    demo: Annotated[
        bool, typer.Option(help="Show the orb's looks with a pretend voice (no Jarvis needed).")
    ] = False,
) -> None:
    """The desktop app (Jarvis.app opens it): Jarvis's menu-bar icon, the orb and your mic.
    Start him from the icon's menu, or with ⌃⌥J."""
    from jarvis.desktop.app import run_desktop  # loads LiveKit and AppKit: only when asked

    raise typer.Exit(run_desktop(get_settings(), demo=demo))


@app.command()
def up() -> None:
    """Start Jarvis now: the desktop app, the LiveKit server and the voice agent, output here."""
    from jarvis.desktop.app import run_desktop

    raise typer.Exit(run_desktop(get_settings(), start_now=True))


@app.command()
def install() -> None:
    """Build Jarvis.app (macOS gives it the mic; it opens at login) and open it."""
    from jarvis.desktop import install as bundle

    try:
        for line in bundle.install(get_settings()):
            console.print(line)
    except bundle.InstallFailed as error:
        console.print(f"[red]{error}[/]")
        raise typer.Exit(1) from None


@app.command()
def uninstall() -> None:
    """Quit and remove Jarvis.app, and stop it opening at login."""
    from jarvis.desktop import install as bundle

    for line in bundle.uninstall():
        console.print(line)


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
    sign_ins.add_row(
        "Notion",
        sign_in_status(
            notion.notion_store(settings), "ask about your notes, or `jarvis login notion`"
        ),
    )
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

    voice = Table(title="Voice (live checks)")
    voice.add_column("Part")
    voice.add_column("Status")
    voice.add_column("Details")
    for part, health in asyncio.run(check_voice(settings)).items():
        status = "[green]ready[/]" if health.present else "[yellow]not ready[/]"
        voice.add_row(part, status, health.detail)
    console.print(voice)


def google_status(settings: Settings) -> str:
    if not google.is_set_up(settings):
        return "[dim]not set up: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env[/]"
    return sign_in_status(
        google.google_store(settings), "ask about your calendar, or run `jarvis login`"
    )


def sign_in_status(store: SignInStore, how_to_sign_in: str) -> str:
    saved = store.saved()
    if saved is None:
        return f"[yellow]not signed in[/]: {how_to_sign_in}"
    renew = store.renew_by(saved).astimezone()
    if store.expired(saved):
        return f"[yellow]needs a fresh sign-in[/] (30 days were up on {renew:%-d %b})"
    return f"[green]signed in[/], until {renew:%-d %b %Y}"
