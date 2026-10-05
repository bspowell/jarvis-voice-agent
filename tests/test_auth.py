from datetime import UTC, datetime, timedelta

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from jarvis.auth import google
from jarvis.auth.google import (
    NOT_SET_UP,
    SCOPES,
    NotSignedIn,
    access_token,
    google_store,
    jarvis_app,
    sign_in,
    sign_out,
)
from jarvis.auth.store import KEYCHAIN_SERVICE, SignInStore
from jarvis.config import Settings

SETTINGS = Settings(
    _env_file=None,
    google_client_id="jarvis.apps.googleusercontent.com",
    google_client_secret="app-secret",
)
DAY = timedelta(days=1)


def google_tokens(expires_in: timedelta = timedelta(hours=1)) -> dict:
    """Saved Google tokens, in the shape Google's library writes them."""
    expiry = datetime.now(UTC) + expires_in
    return {
        "token": "access-1",
        "refresh_token": "refresh-1",
        "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scopes": SCOPES,
    }


def test_a_sign_in_lasts_30_days():
    store = SignInStore("google", remember_days=30)
    signed_in = datetime(2026, 10, 4, tzinfo=UTC)
    store.save(google_tokens(), signed_in_at=signed_in)
    assert store.load(now=signed_in + 29 * DAY) is not None
    assert store.load(now=signed_in + 30 * DAY) is None  # time to approve Jarvis again


def test_a_damaged_keychain_entry_counts_as_signed_out(keychain):
    keychain.set_password(KEYCHAIN_SERVICE, "google", "not json")
    assert SignInStore("google", remember_days=30).load() is None


async def test_a_sign_in_from_before_tasks_asks_for_the_new_permission():
    calendar_only = {**google_tokens(), "scopes": SCOPES[:2]}  # saved before Jarvis did tasks
    google_store(SETTINGS).save(calendar_only)
    with pytest.raises(NotSignedIn, match="one more Google permission"):
        await access_token(SETTINGS)


async def test_a_fresh_access_token_is_used_as_it_is():
    google_store(SETTINGS).save(google_tokens())
    assert await access_token(SETTINGS) == "access-1"


async def test_an_expired_access_token_is_renewed_silently(monkeypatch):
    signed_in = datetime.now(UTC) - 3 * DAY
    google_store(SETTINGS).save(google_tokens(-timedelta(minutes=5)), signed_in_at=signed_in)

    def renew(credentials: Credentials, request) -> None:  # stands in for Google's token service
        credentials.token = "access-2"
        credentials.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", renew)
    assert await access_token(SETTINGS) == "access-2"
    saved = google_store(SETTINGS).load()
    assert saved.token["token"] == "access-2"  # kept for next time
    assert saved.signed_in_at == signed_in  # renewing doesn't restart the 30 days


async def test_no_sign_in_means_not_signed_in():
    with pytest.raises(NotSignedIn):
        await access_token(SETTINGS)


async def test_removed_access_means_not_signed_in(monkeypatch):
    google_store(SETTINGS).save(google_tokens(-timedelta(minutes=5)))

    def renew(credentials: Credentials, request) -> None:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", renew)
    with pytest.raises(NotSignedIn, match="stopped working"):
        await access_token(SETTINGS)


def test_sign_in_uses_pkce_and_keeps_jarvis_app_details_out_of_the_keychain(monkeypatch):
    asked = {}

    def browser_sign_in(flow: InstalledAppFlow, **options) -> Credentials:
        """Stands in for the browser round trip: builds the consent link, then "approves"."""
        flow.redirect_uri = "http://localhost:51234/"
        asked["link"], _ = flow.authorization_url(prompt=options["prompt"])
        asked["options"] = options
        return Credentials(
            "access-1",
            refresh_token="refresh-1",
            client_id=SETTINGS.google_client_id,
            client_secret="app-secret",
            scopes=SCOPES,
        )

    monkeypatch.setattr(InstalledAppFlow, "run_local_server", browser_sign_in)
    sign_in(SETTINGS)

    link = asked["link"]
    assert "code_challenge_method=S256" in link  # PKCE: a one-time secret per sign-in
    assert "access_type=offline" in link  # a refresh token, for silent renewals
    assert "prompt=consent" in link
    assert "calendar.events" in link and "calendar.calendarlist.readonly" in link
    assert asked["options"]["port"] == 0  # any free port on this Mac
    saved = google_store(SETTINGS).load()
    assert saved.token["refresh_token"] == "refresh-1"
    assert "client_secret" not in saved.token  # Jarvis's app details stay in .env


def test_sign_out_forgets_and_tells_google(monkeypatch):
    google_store(SETTINGS).save(google_tokens())
    revoked = []
    monkeypatch.setattr(google.httpx, "post", lambda url, data, timeout: revoked.append(data))
    assert sign_out(SETTINGS) is True
    assert revoked == [{"token": "refresh-1"}]
    assert google_store(SETTINGS).load() is None
    assert sign_out(SETTINGS) is False  # already signed out


def test_sign_in_needs_jarvis_google_app_in_env():
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        jarvis_app(Settings(_env_file=None))
    assert "GOOGLE_CLIENT_SECRET" in NOT_SET_UP
