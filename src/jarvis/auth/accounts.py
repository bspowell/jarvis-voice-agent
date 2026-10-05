"""The accounts Jarvis signs in to, as every front end (chat, voice) names and opens them."""

import asyncio
import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from jarvis.auth import google, notion
from jarvis.config import Settings


class Service(StrEnum):
    """The online accounts Jarvis signs in to."""

    google = "google"
    notion = "notion"


@dataclass(frozen=True)
class Account:
    """The words for one service's sign-in."""

    name: str  # "Google"
    button: str  # the sign-in button's label
    signed_in: str  # sent for you once you've signed in, so Jarvis carries on with your request


ACCOUNTS = {
    Service.google: Account("Google", "Sign in with Google", "I've signed in to Google."),
    Service.notion: Account("Notion", "Connect Notion", "I've connected Notion."),
}


async def sign_in(settings: Settings, service: Service) -> None:
    """Open the service's consent page in your browser and wait for you (raises SignInFailed)."""
    if service == Service.google:
        await on_background_thread(lambda: google.sign_in(settings))  # Google's library blocks
    else:
        await notion.sign_in(settings)


async def on_background_thread(work: Callable[[], None]) -> None:
    """Wait for blocking `work` on a daemon thread, which never stops Jarvis from quitting."""
    loop = asyncio.get_running_loop()
    finished: asyncio.Future[None] = loop.create_future()

    def report(error: BaseException | None) -> None:
        if finished.done():  # nobody's waiting any more
            return
        if error is None:
            finished.set_result(None)
        else:
            finished.set_exception(error)

    def run() -> None:
        error = None
        try:
            work()
        except BaseException as problem:
            error = problem
        with contextlib.suppress(RuntimeError):  # the loop closed while it ran: nothing to tell
            loop.call_soon_threadsafe(report, error)

    threading.Thread(target=run, daemon=True).start()
    await finished
