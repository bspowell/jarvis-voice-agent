"""The desktop app's second thread: an asyncio loop for everything that waits.

macOS keeps the main thread busy with its event loop (windows, the menu-bar icon), so the
waiting happens here: starting and stopping the crew, the room with Jarvis, sign-ins. The main
thread hands work over with `call` (a plain function) or `submit` (a coroutine); both are safe
from any thread.
"""

import asyncio
import contextlib
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any


class Line:
    """An asyncio loop running on a thread of its own until `close`."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        # A daemon thread: if the app must exit in a hurry, this never holds it up.
        self.thread = threading.Thread(target=self.run, name="line-to-jarvis", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, work: Callable[..., Any], *args: Any) -> None:
        """Run `work(*args)` on the line's loop, soon."""
        with contextlib.suppress(RuntimeError):  # the loop has closed: the app is exiting
            self.loop.call_soon_threadsafe(work, *args)

    def submit[T](self, work: Coroutine[Any, Any, T]) -> Future[T]:
        """Run a coroutine on the line's loop; the future holds its result."""
        return asyncio.run_coroutine_threadsafe(work, self.loop)

    def close(self, seconds: float = 5.0) -> None:
        """Stop the loop (after whatever is running now) and wait for the thread."""
        self.call(self.loop.stop)
        self.thread.join(seconds)
