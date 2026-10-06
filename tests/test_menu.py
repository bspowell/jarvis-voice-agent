"""What the menu-bar icon's menu says, for each state Jarvis can be in, and how each account
stands (read from the Keychain: here, the tests' in-memory one)."""

from datetime import UTC, datetime, timedelta

from jarvis.auth.accounts import Service
from jarvis.auth.google import google_store
from jarvis.auth.notion import notion_store
from jarvis.config import Settings
from jarvis.desktop.menu import (
    SEPARATOR,
    Connection,
    Snapshot,
    connection_of,
    icon_look,
    menu_items,
    mood,
    start_option,
    status_of,
)

SETTINGS = Settings(
    _env_file=None, google_client_id="jarvis.apps.googleusercontent.com", google_client_secret="s"
)
ACCOUNTS = (
    Connection(Service.google, "signed in", datetime(2026, 11, 5, 12, tzinfo=UTC)),
    Connection(Service.notion, "signed out"),
)


def titles(snapshot: Snapshot) -> list[str]:
    return [item.title for item in menu_items(snapshot) if item != SEPARATOR]


def item(snapshot: Snapshot, title: str):
    return next(entry for entry in menu_items(snapshot) if entry.title == title)


def test_on_the_menu_is_the_one_you_asked_for():
    on = Snapshot(state="on", mood="asleep", connections=ACCOUNTS, open_at_login=True)
    assert titles(on) == [
        "Jarvis is asleep: say “Hey Jarvis”",
        *("Talk to Jarvis", "Mute Jarvis", "Stop Jarvis"),
        "Start Options",
        *("Hide Orb", "Orb Visual"),
        *("Google: connected", "Notion: disconnected"),
        "Quit Jarvis",
    ]
    talk, mute, quit_ = item(on, "Talk to Jarvis"), item(on, "Mute Jarvis"), item(on, "Quit Jarvis")
    assert (talk.shortcut, talk.icon) == ("⌃⌥J", "waveform")
    assert (mute.shortcut, mute.icon) == ("⌃⌥M", "mic.slash")
    assert (quit_.shortcut, quit_.icon) == ("⌘Q", "power")
    assert item(on, "Jarvis is asleep: say “Hey Jarvis”").dot == "green"


def test_off_the_menu_offers_to_start_him():
    off = Snapshot(state="off", open_at_login=True)
    assert titles(off) == [
        "Jarvis is off",
        "Start Jarvis",
        "Start Options",
        "Orb Visual",
        "Quit Jarvis",
    ]
    start = item(off, "Start Jarvis")
    assert (start.action, start.shortcut) == ("start", "⌃⌥J")
    assert item(off, "Jarvis is off").dot == "grey"


def test_after_a_failure_the_menu_says_what_went_wrong_and_offers_the_log():
    failed = Snapshot(state="off", problem="the agent stopped by itself (exit code 1).")
    assert titles(failed)[:2] == [
        "Jarvis stopped: the agent stopped by itself (exit code 1).",
        "Show Log",
    ]
    assert item(failed, titles(failed)[0]).dot == "red"


def test_muted_the_menu_offers_to_unmute():
    muted = Snapshot(state="on", mood="asleep", muted=True)
    unmute = item(muted, "Unmute Jarvis")
    assert (unmute.action, unmute.icon) == ("mute", "mic")
    assert status_of(muted) == ("Jarvis is muted: he can't hear you", "red")
    assert status_of(Snapshot(state="on", mood="speaking", muted=True)) == (
        "Jarvis is speaking",  # he can finish a reply; he just can't hear you
        "green",
    )


def test_while_starting_only_stop_works():
    starting = Snapshot(state="starting")
    assert status_of(starting) == ("Jarvis is starting…", "orange")
    assert not item(starting, "Talk to Jarvis").enabled
    assert item(starting, "Stop Jarvis").enabled
    assert not item(Snapshot(state="stopping"), "Stop Jarvis").enabled


def test_start_options_are_three_choices():
    def chosen(snapshot: Snapshot) -> list[str]:
        options = item(snapshot, "Start Options").submenu
        return [entry.title for entry in options if entry.checked]

    assert chosen(Snapshot(state="off", open_at_login=True)) == ["Show in menu bar at login"]
    launch = Snapshot(state="off", open_at_login=True, start_automatically=True)
    assert chosen(launch) == ["Launch at login"]
    assert start_option(launch) == "launch"
    assert chosen(Snapshot(state="off", open_at_login=False)) == ["Don't start at login"]
    options = item(Snapshot(state="off", open_at_login=True), "Start Options").submenu
    assert [(entry.action, entry.argument) for entry in options] == [
        *(("start_option", "launch"), ("start_option", "menu_bar"), ("start_option", "off"))
    ]


def test_start_options_need_jarvis_app():
    options = item(Snapshot(state="off", open_at_login=None), "Start Options").submenu
    assert not any(entry.enabled for entry in options if entry.action)
    assert options[-1].title == "Needs Jarvis.app: uv run jarvis install"


def test_the_orb_visual_is_ticked_and_the_orb_can_be_shown_again():
    on = Snapshot(state="on", mood="asleep", core="infinity-war", orb_showing=False)
    visuals = item(on, "Orb Visual").submenu
    assert [entry.title for entry in visuals] == [
        *("Jarvis", "Arc Reactor 1", "Arc Reactor 2", "Mjölnir", "Stormbreaker")
    ]
    assert [entry.argument for entry in visuals if entry.checked] == ["infinity-war"]
    assert item(on, "Show Orb").icon == "eye"


def test_accounts_connect_with_a_click_and_disconnect_from_their_submenu():
    snapshot = Snapshot(state="off", connections=ACCOUNTS)
    google = item(snapshot, "Google: connected")
    assert [entry.title for entry in google.submenu] == [
        "Connected until 5 Nov",
        "Disconnect Google",
    ]
    assert (google.submenu[1].action, google.submenu[1].argument) == ("sign_out", "google")
    notion = item(snapshot, "Notion: disconnected")
    assert (notion.action, notion.argument, notion.submenu) == ("sign_in", "notion", ())
    connecting = Snapshot(state="off", connections=(Connection(Service.notion, "signing in"),))
    assert not item(connecting, "Notion: connecting…").enabled


def test_the_icon_shows_listening_and_muted():
    assert icon_look(Snapshot(state="off")) == "asleep"  # drawn faded while he's off
    assert icon_look(Snapshot(state="on", mood="asleep")) == "asleep"
    assert icon_look(Snapshot(state="on", mood="thinking")) == "awake"
    assert icon_look(Snapshot(state="on", mood="listening", muted=True)) == "muted"


def test_the_orbs_mood_as_orb_js_decides_it():
    assert mood({}) == "away"
    assert mood({"state": "listening", "awake": False}) == "asleep"
    assert mood({"state": "listening", "awake": True}) == "listening"
    assert mood({"state": "speaking", "awake": True}) == "speaking"


def test_accounts_are_read_from_the_keychain():
    now = datetime.now(UTC)
    assert connection_of(SETTINGS, Service.google, False).status == "signed out"
    google_store(SETTINGS).save({"token": "t"}, signed_in_at=now - timedelta(days=2))
    google = connection_of(SETTINGS, Service.google, False)
    assert (google.status, google.until) == ("signed in", now + timedelta(days=28))
    notion_store(SETTINGS).save({}, signed_in_at=now - timedelta(days=31))
    assert connection_of(SETTINGS, Service.notion, False).status == "renew"  # 30 days are up
    assert connection_of(SETTINGS, Service.notion, True).status == "signing in"
    unset = Settings(_env_file=None)
    assert connection_of(unset, Service.google, False).status == "not set up"
