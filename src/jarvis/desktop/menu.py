"""What the menu-bar icon's menu says, made from a snapshot of how things are.

Plain Python, no AppKit: menu_bar.py turns the items into macOS's menu (rebuilt each time it
opens) and hands back the action of the item you click. Like cards.py for the cards.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from jarvis.auth import google, notion
from jarvis.auth.accounts import ACCOUNTS, Service
from jarvis.auth.store import SignInStore
from jarvis.config import Settings
from jarvis.desktop.controls import State

TALK_KEY = "j"  # ⌃⌥J: start Jarvis, or he listens to you now
MUTE_KEY = "m"  # ⌃⌥M: mute or unmute your mic
Status = Literal["signed in", "signed out", "renew", "not set up", "signing in"]
START_OPTIONS = (  # what happens at login (an argument for the "start_option" action)
    ("launch", "Launch at login"),  # the icon appears and Jarvis starts
    ("menu_bar", "Show in menu bar at login"),  # only the icon appears
    ("off", "Don't start at login"),
)
ORB_VISUALS = (  # what's in the orb's middle, as the menu names them (cores.js)
    ("jarvis", "Jarvis"),
    ("iron-man-1", "Arc Reactor 1"),
    ("infinity-war", "Arc Reactor 2"),
    ("mjolnir", "Mjölnir"),
    ("stormbreaker", "Stormbreaker"),
)


@dataclass(frozen=True)
class Connection:
    """An account Jarvis signs in to, as the menu shows it."""

    service: Service
    status: Status
    until: datetime | None = None  # signed in: when Jarvis will ask you to sign in again
    days: int = 30  # how often he asks (`[auth] remember_days`)


@dataclass(frozen=True)
class Snapshot:
    """Everything the menu and the icon show."""

    state: State  # off / starting / on / stopping
    mood: str = "away"  # what the orb shows: away / asleep / listening / thinking / speaking
    muted: bool = False
    problem: str | None = None  # why he stopped by himself, last time
    orb_showing: bool = True
    core: str = "jarvis"  # the orb's centrepiece
    connections: tuple[Connection, ...] = ()
    start_automatically: bool = False
    open_at_login: bool | None = None  # None: Jarvis.app isn't installed (`jarvis install`)


@dataclass(frozen=True)
class Item:
    """One line of the menu."""

    title: str
    action: str | None = None  # what a click does (a name menu_bar.py knows); None: just words
    argument: str | None = None  # e.g. which account
    icon: str | None = None  # a macOS symbol (SF Symbols), shown before the title
    dot: str | None = None  # a coloured dot instead (the status line): green, orange, red, grey
    checked: bool = False
    enabled: bool = True
    shortcut: str | None = None  # shown beside it, e.g. "⌃⌥J"
    hint: str | None = None  # shown when the pointer rests on it
    submenu: tuple["Item", ...] = field(default=())


SEPARATOR = Item("-")


def menu_items(snapshot: Snapshot) -> list[Item]:
    """The menu, top to bottom: how he is, then talking to him, starting, the orb, accounts."""
    state = snapshot.state
    words, dot = status_of(snapshot)
    items = [Item(words, dot=dot)]
    if state == "off" and snapshot.problem:
        items.append(Item("Show Log", "show_log", icon="doc.text"))
    items.append(SEPARATOR)
    if state == "off":
        items.append(Item("Start Jarvis", "start", icon="play.fill", shortcut="⌃⌥J"))
    else:
        on, muted = state == "on", snapshot.muted
        items += [
            Item("Talk to Jarvis", "talk", icon="waveform", enabled=on, shortcut="⌃⌥J"),
            Item(
                "Unmute Jarvis" if muted else "Mute Jarvis",
                "mute",
                icon="mic" if muted else "mic.slash",
                enabled=on,
                shortcut="⌃⌥M",
                hint="Your mic: muted, Jarvis can't hear you",
            ),
            Item("Stop Jarvis", "stop", icon="stop.fill", enabled=state != "stopping"),
        ]
    items += [SEPARATOR, start_options(snapshot), SEPARATOR]
    if state == "on":
        showing = snapshot.orb_showing
        items.append(
            Item(
                "Hide Orb" if showing else "Show Orb",
                "orb",
                icon="eye.slash" if showing else "eye",
            )
        )
    visuals = tuple(
        Item(name, "core", core, checked=core == snapshot.core) for core, name in ORB_VISUALS
    )
    items += [Item("Orb Visual", icon="atom", submenu=visuals), SEPARATOR]
    items += [connection_item(connection) for connection in snapshot.connections]
    items += [SEPARATOR, Item("Quit Jarvis", "quit", icon="power", shortcut="⌘Q")]
    return items


def status_of(snapshot: Snapshot) -> tuple[str, str]:
    """The menu's first line (also the icon's tooltip), and its dot's colour."""
    state, mood = snapshot.state, snapshot.mood
    if state == "off":
        if snapshot.problem:
            return f"Jarvis stopped: {snapshot.problem}", "red"
        return "Jarvis is off", "grey"
    if state in ("starting", "stopping"):
        return f"Jarvis is {state}…", "orange"
    if snapshot.muted and mood not in ("thinking", "speaking"):
        return "Jarvis is muted: he can't hear you", "red"
    if mood == "away":
        return "Jarvis is on his way…", "orange"
    if mood == "asleep":
        return "Jarvis is asleep: say “Hey Jarvis”", "green"
    return f"Jarvis is {mood}", "green"


def status_line(snapshot: Snapshot) -> str:
    return status_of(snapshot)[0]


def start_option(snapshot: Snapshot) -> str:
    """Which start option is chosen: launch, menu_bar or off."""
    if not snapshot.open_at_login:
        return "off"
    return "launch" if snapshot.start_automatically else "menu_bar"


def start_options(snapshot: Snapshot) -> Item:
    """What happens at login: three choices (they need Jarvis.app, which login opens)."""
    installed = snapshot.open_at_login is not None
    chosen = start_option(snapshot)
    choices = tuple(
        Item(name, "start_option", option, checked=option == chosen, enabled=installed)
        for option, name in START_OPTIONS
    )
    if not installed:
        choices += (SEPARATOR, Item("Needs Jarvis.app: uv run jarvis install"))
    return Item("Start Options", icon="gearshape", submenu=choices)


def connection_item(connection: Connection) -> Item:
    """ "Google: connected" (a submenu to disconnect) or "Google: disconnected" (click to
    connect: the browser opens)."""
    service, status = connection.service, connection.status
    name = ACCOUNTS[service].name
    if status == "signed in":
        until = Item(f"Connected until {date_of(connection.until)}")
        disconnect = Item(f"Disconnect {name}", "sign_out", service.value)
        return Item(
            f"{name}: connected",
            icon="person.crop.circle.badge.checkmark",
            submenu=(until, disconnect),
        )
    if status == "signing in":
        return Item(
            f"{name}: connecting…",
            icon="person.crop.circle.badge.clock",
            enabled=False,
            hint="Finish in your browser",
        )
    if status == "not set up":
        return Item(
            f"{name}: not set up",
            icon="person.crop.circle.badge.xmark",
            enabled=False,
            hint="Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env",
        )
    again = f"Jarvis asks every {connection.days} days. " if status == "renew" else ""
    return Item(
        f"{name}: disconnected",
        "sign_in",
        service.value,
        icon="person.crop.circle.badge.xmark",
        hint=f"{again}Click to connect (your browser opens)",
    )


def date_of(moment: datetime | None) -> str:
    return f"{moment.astimezone():%-d %b}" if moment else "?"


def connection_of(
    settings: Settings, service: Service, signing_in: bool, now: datetime | None = None
) -> Connection:
    """How an account stands, from the Keychain (a few milliseconds)."""
    if signing_in:
        return Connection(service, "signing in")
    if service == Service.google and not google.is_set_up(settings):
        return Connection(service, "not set up")
    stores = {Service.google: google.google_store, Service.notion: notion.notion_store}
    store: SignInStore = stores[service](settings)
    saved = store.saved()
    if saved is None:
        return Connection(service, "signed out")
    days = settings.auth.remember_days
    if store.expired(saved, now or datetime.now(UTC)):
        return Connection(service, "renew", days=days)
    return Connection(service, "signed in", store.renew_by(saved), days)


def mood(latest: Mapping[str, Any]) -> str:
    """What the orb shows, from what it was told (as orb.js decides it)."""
    state = latest.get("state", "away")
    if state in ("away", "thinking", "speaking"):
        return str(state)
    return "listening" if latest.get("awake") else "asleep"


def icon_look(snapshot: Snapshot) -> str:
    """Which icon: asleep (two rings), awake (a dot in the middle) or muted (a slash)."""
    if snapshot.state == "on" and snapshot.muted:
        return "muted"
    if snapshot.state == "on" and snapshot.mood in ("listening", "thinking", "speaking"):
        return "awake"
    return "asleep"
