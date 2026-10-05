"""Where sign-ins live: the macOS Keychain, plus Jarvis's 30-day rule.

Each service (Google, Notion) gets one Keychain entry, named "jarvis" with the service as its
account. It holds the service's tokens and the moment you signed in. Tokens are renewed
silently, but the sign-in date only moves when you sign in again, so every `remember_days` (30)
Jarvis asks you to approve him in the browser once more.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import keyring
from keyring.errors import PasswordDeleteError

KEYCHAIN_SERVICE = "jarvis"  # the name to search for in Keychain Access


class NotSignedIn(Exception):
    """No working sign-in: never signed in, signed out, access removed, or 30 days up."""


class SignInFailed(Exception):
    """The browser sign-in didn't finish: timed out, declined, or a permission was unticked."""


@dataclass(frozen=True)
class SignIn:
    token: dict[str, Any]  # the service's tokens: refresh token, access token, expiry…
    signed_in_at: datetime  # when you last approved Jarvis in the browser (UTC)


class SignInStore:
    """One service's sign-in, kept in the Keychain."""

    def __init__(self, service: str, remember_days: int) -> None:
        self.service = service  # e.g. "google"
        self.remember_for = timedelta(days=remember_days)

    def load(self, now: datetime | None = None) -> SignIn | None:
        """The saved sign-in, or None if there isn't one or it's past `remember_days`."""
        saved = self.saved()
        if saved and self.expired(saved, now):
            return None  # time to confirm again: the next use shows the sign-in button
        return saved

    def saved(self) -> SignIn | None:
        """Whatever the Keychain holds, expired or not. None if empty or unreadable."""
        entry = keyring.get_password(KEYCHAIN_SERVICE, self.service)
        if entry is None:
            return None
        try:
            data = json.loads(entry)
            return SignIn(data["token"], datetime.fromisoformat(data["signed_in_at"]))
        except (ValueError, KeyError, TypeError):  # damaged entry: same as signed out
            return None

    def expired(self, sign_in: SignIn, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.renew_by(sign_in)

    def renew_by(self, sign_in: SignIn) -> datetime:
        """When Jarvis will ask you to sign in again."""
        return sign_in.signed_in_at + self.remember_for

    def save(self, token: dict[str, Any], signed_in_at: datetime | None = None) -> None:
        """Save tokens. After a silent renewal, pass the old `signed_in_at`: the 30 days run on."""
        data = {"token": token, "signed_in_at": (signed_in_at or datetime.now(UTC)).isoformat()}
        keyring.set_password(KEYCHAIN_SERVICE, self.service, json.dumps(data))

    def forget(self) -> bool:
        """Delete the sign-in. False if there was none."""
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, self.service)
        except PasswordDeleteError:
            return False
        return True
