"""Connecting Notion: approve Jarvis once in your browser; he renews his access silently.

Notion runs its own MCP server, and FastMCP (the library under LangChain's MCP support) signs in
to it with OAuth:
1. Jarvis registers himself with Notion on first use ("dynamic client registration"), so unlike
   Google there's no developer app to set up by hand.
2. Your browser opens Notion's consent page: pick the workspace and approve.
3. Notion sends the browser back to a one-off listener on your Mac with a one-time code, which
   Jarvis swaps for tokens. PKCE proves it's the same Jarvis that asked.
4. Everything is kept in the Keychain (entry "jarvis" / "notion"). FastMCP renews the access
   token silently; the 30-day rule still applies.

Tool calls never open a browser: without a working sign-in they raise `NotSignedIn`, and Jarvis
shows the "Connect Notion" button instead.
"""

import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, SupportsFloat

import httpx
from fastmcp.client import Client
from fastmcp.client.auth import OAuth

from jarvis.auth.store import NotSignedIn, SignInFailed, SignInStore
from jarvis.config import Settings

SERVICE = "notion"
NOTION_MCP = "https://mcp.notion.com/mcp"
REVOKE_URI = "https://mcp.notion.com/token"  # where Notion's sign-in details say to cancel access
SIGN_IN_TIMEOUT_SECONDS = 300  # stop waiting for the browser after five minutes

# The MCP libraries log every sign-in step, plus a full traceback whenever no sign-in is saved
# (normal for Jarvis: he shows the Connect button instead). Jarvis reports these himself.
logging.getLogger("fastmcp").setLevel(logging.WARNING)
logging.getLogger("mcp.client.auth").setLevel(logging.CRITICAL)


def notion_store(settings: Settings) -> SignInStore:
    return SignInStore(SERVICE, settings.auth.remember_days)


def is_signed_in(settings: Settings) -> bool:
    """A Notion sign-in is saved and within its 30 days. (Connecting proves it still works.)"""
    return notion_store(settings).load() is not None


class KeychainTokens:
    """FastMCP's sign-in storage, kept in one Keychain entry with the 30-day rule.

    FastMCP saves three things per server through a small key-value interface: Jarvis's
    registration with Notion, the tokens, and when the access token expires. `get`, `put` and
    `delete` are all it calls. Its expiry times (`ttl`) are ignored: the 30-day rule decides.
    """

    def __init__(self, store: SignInStore) -> None:
        self.store = store

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        saved = self.store.load()  # nothing once the 30 days are up: Notion counts as signed out
        return saved.token.get(entry_name(key, collection)) if saved else None

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        saved = self.store.saved()
        entries = {**(saved.token if saved else {}), entry_name(key, collection): dict(value)}
        # Renewed tokens keep the sign-in date, so the 30 days run on.
        self.store.save(entries, signed_in_at=saved.signed_in_at if saved else None)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        saved = self.store.saved()
        name = entry_name(key, collection)
        if saved is None or name not in saved.token:
            return False
        rest = {other: value for other, value in saved.token.items() if other != name}
        self.store.save(rest, signed_in_at=saved.signed_in_at)
        return True


def entry_name(key: str, collection: str | None) -> str:
    return f"{collection}:{key}" if collection else key


class NotionOAuth(OAuth):
    """FastMCP's OAuth for Notion, except that only the Connect button may open a browser.

    With no working tokens, FastMCP would open Notion's consent page in the middle of a tool
    call and wait up to five minutes for you. Jarvis raises `NotSignedIn` instead.
    """

    def __init__(self, settings: Settings, browser: bool) -> None:
        super().__init__(
            mcp_url=NOTION_MCP,
            client_name="Jarvis",  # the name Notion's consent page shows
            token_storage=KeychainTokens(notion_store(settings)),
            callback_timeout=SIGN_IN_TIMEOUT_SECONDS,
            # A "public client": an app on your Mac can't keep a secret, so it gets none, and
            # PKCE protects the sign-in. (Notion's default, a secret, broke the token exchange.)
            additional_client_metadata={"token_endpoint_auth_method": "none"},
        )
        self.browser = browser

    async def redirect_handler(self, authorization_url: str) -> None:
        if not self.browser:
            raise NotSignedIn("Not connected to Notion.")
        await super().redirect_handler(authorization_url)


def notion_client(settings: Settings, browser: bool = False) -> Client:
    """A client for Notion's MCP server, signed in with the tokens in the Keychain."""
    return Client(NOTION_MCP, auth=NotionOAuth(settings, browser))


async def sign_in(settings: Settings) -> None:
    """Open Notion's consent page and wait for your approval (up to five minutes)."""
    store = notion_store(settings)
    store.forget()  # start fresh: a new registration with Notion and a new 30 days
    try:
        async with notion_client(settings, browser=True):  # connecting runs the sign-in
            pass
    except BaseException as error:
        store.forget()  # keep nothing half-done (e.g. a registration without tokens)
        if isinstance(error, RuntimeError):  # FastMCP's "Client failed to connect: <cause>"
            raise SignInFailed(sign_in_problem(error)) from error
        raise


def sign_in_problem(error: RuntimeError) -> str:
    """Why the browser sign-in failed, in words for the chat."""
    cause = error.__cause__
    if isinstance(cause, TimeoutError):
        return "No answer from the browser within five minutes."
    return f"Notion said: {cause or error}"  # e.g. you pressed Cancel: "Access was denied…"


def stopped_working(error: BaseException) -> bool:
    """True if connecting failed because the saved sign-in no longer works (e.g. access removed)."""
    return isinstance(error, RuntimeError) and isinstance(error.__cause__, NotSignedIn)


def sign_out(settings: Settings) -> bool:
    """Forget the Notion sign-in, and ask Notion to cancel Jarvis's access. False if signed out."""
    store = notion_store(settings)
    saved = store.saved()
    if saved is None:
        return False
    found = {name: value for entry in saved.token.values() for name, value in entry.items()}
    if "refresh_token" in found and "client_id" in found:
        with suppress(httpx.HTTPError):  # offline: forgetting it here still signs out this Mac
            httpx.post(
                REVOKE_URI,
                data={"token": found["refresh_token"], "client_id": found["client_id"]},
                timeout=10,
            )
    return store.forget()
