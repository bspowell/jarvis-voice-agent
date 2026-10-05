"""Signing in to Google: approve Jarvis once in your browser; he renews his access silently.

Google's sign-in for desktop apps, in four steps:
1. Jarvis starts a one-off listener on your Mac (http://localhost:<free port>/).
2. Your browser opens Google's consent page, and you approve.
3. Google sends the browser back to that address with a one-time code, which Jarvis swaps for
   tokens. PKCE (a secret made fresh for each sign-in) proves it's the same Jarvis that asked,
   so a stolen code is useless to anyone else.
4. The tokens go to the Keychain. The access token lasts an hour; the refresh token renews it.
"""

import asyncio
import json
from contextlib import suppress
from typing import Any

import httpx
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from oauthlib.oauth2 import OAuth2Error

from jarvis.auth.store import NotSignedIn, SignInFailed, SignInStore
from jarvis.config import Settings

SERVICE = "google"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",  # see and edit events on all your calendars
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",  # which calendars you have
]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
SIGN_IN_TIMEOUT_SECONDS = 300  # stop waiting for the browser after five minutes
SIGNED_IN_PAGE = "Jarvis is signed in to your Google Calendar. You can close this tab."
APP_FIELDS = ["client_id", "client_secret"]  # Jarvis's app (in .env), never stored with your tokens
NOT_SET_UP = "Google sign-in isn't set up: add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env."


def is_set_up(settings: Settings) -> bool:
    return settings.google_client_id is not None and settings.google_client_secret is not None


def google_store(settings: Settings) -> SignInStore:
    return SignInStore(SERVICE, settings.auth.remember_days)


def jarvis_app(settings: Settings) -> dict[str, str]:
    """Jarvis's own Google sign-in app: the Desktop OAuth client from .env."""
    client_id, client_secret = settings.google_client_id, settings.google_client_secret
    if client_id is None or client_secret is None:
        raise RuntimeError(NOT_SET_UP)
    return {"client_id": client_id, "client_secret": client_secret.get_secret_value()}


def sign_in(settings: Settings) -> None:
    """Open Google's consent page and wait for your approval. Blocks for up to five minutes."""
    flow = InstalledAppFlow.from_client_config(
        {"installed": {**jarvis_app(settings), "auth_uri": AUTH_URI, "token_uri": TOKEN_URI}},
        SCOPES,
    )
    try:
        credentials = flow.run_local_server(
            port=0,  # any free port: Google accepts any localhost address for desktop apps
            authorization_prompt_message=None,  # the chat says what's happening instead
            success_message=SIGNED_IN_PAGE,
            timeout_seconds=SIGN_IN_TIMEOUT_SECONDS,
            prompt="consent",  # always ask: then Google always sends a refresh token
        )
    except WSGITimeoutError as error:
        raise SignInFailed("No answer from the browser within five minutes.") from error
    except OAuth2Error as error:  # e.g. you pressed Cancel on Google's page
        raise SignInFailed(f"Google said: {error.error}.") from error
    except Warning as error:  # how oauthlib reports a permission you unticked
        raise SignInFailed(
            "Jarvis needs both calendar permissions. Sign in again and tick both boxes."
        ) from error
    google_store(settings).save(tokens_of(credentials))


def tokens_of(credentials: Credentials) -> dict[str, Any]:
    """Your tokens, without Jarvis's app details (those stay in .env)."""
    return json.loads(credentials.to_json(strip=APP_FIELDS))


async def access_token(settings: Settings) -> str:
    """A working access token for Google's APIs, renewed silently once its hour is up.

    Raises NotSignedIn when there's no working sign-in, so callers can offer the button instead.
    """
    store = google_store(settings)
    saved = store.load()
    if saved is None:
        raise NotSignedIn("Not signed in to Google.")
    credentials = Credentials.from_authorized_user_info({**saved.token, **jarvis_app(settings)})
    if not credentials.valid:  # its hour is up
        try:
            # Google's library is synchronous: run it on a thread so Jarvis isn't blocked.
            await asyncio.to_thread(credentials.refresh, Request())
        except RefreshError as error:  # e.g. you removed Jarvis's access in your Google account
            raise NotSignedIn(f"Google sign-in stopped working ({error}).") from error
        store.save(tokens_of(credentials), signed_in_at=saved.signed_in_at)  # the 30 days run on
    return credentials.token


def sign_out(settings: Settings) -> bool:
    """Forget the Google sign-in, and ask Google to cancel Jarvis's access. False if signed out."""
    store = google_store(settings)
    saved = store.saved()
    if saved is None:
        return False
    token = saved.token.get("refresh_token") or saved.token.get("token")
    with suppress(httpx.HTTPError):  # offline: forgetting it here still signs out this Mac
        httpx.post(REVOKE_URI, data={"token": token}, timeout=10)
    return store.forget()
