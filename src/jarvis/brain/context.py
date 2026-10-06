"""What the front end hands Jarvis with every message: LangChain's "runtime context".

The conversation (state) is saved between turns; the context isn't. It's passed in fresh on each
run, so it can carry live things, such as a function that reaches the desktop app. Tools read it
from `runtime.context`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from jarvis.display import DisplayCommand

Display = Callable[[DisplayCommand], Awaitable[str]]  # carries out a command, says what it did


@dataclass(frozen=True)
class JarvisContext:
    display: Display | None = None  # the orb's controls; None where there's no orb (text chat)
