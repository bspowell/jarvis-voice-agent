"""The orb's window: see-through, above every window and on every desktop (fullscreen apps too),
and your clicks go straight through it. Made from macOS's own AppKit + WebKit, via PyObjC.

The orb itself is a web page (web/orb.html) drawn with WebGL. Python only tells it what's
happening: `orb.update({state, awake, level, core})`. Where you put it and what's in its middle
are remembered between launches (data/orb.json).

AppKit only works on the main thread: build and change the window there. Two methods are safe
from any thread: `update` and `apply` (they pass the work to the main thread).
"""

import asyncio
import json
import logging
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, get_args

import AppKit
import Foundation
import objc
import WebKit
from PyObjCTools import AppHelper

from jarvis.display import CORE_NAMES, Core, Corner, DisplayCommand

logger = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "web" / "orb.html"
SIZE = 280  # the window, in points (the orb grows and shrinks inside it)
MARGIN = 12  # from the screen's edges, menu bar and Dock excluded
ON_EVERY_DESKTOP = (
    AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces  # every desktop (Space)
    | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary  # over fullscreen apps too
    | AppKit.NSWindowCollectionBehaviorStationary  # stays put when you swipe between desktops
    | AppKit.NSWindowCollectionBehaviorIgnoresCycle  # never a target for Cmd-`
)


def corner_origin(
    area: tuple[float, float, float, float], size: float, corner: Corner
) -> tuple[float, float]:
    """Where the window goes: its bottom-left point, in macOS's coordinates (y counts up).
    `area` is the screen's usable part: x, y, width, height."""
    x, y, width, height = area
    left = x + MARGIN if corner.endswith("left") else x + width - size - MARGIN
    bottom = y + height - size - MARGIN if corner.startswith("top") else y + MARGIN
    return left, bottom


@dataclass
class Remembered:
    """What the orb keeps between launches: where it was, and what's in its middle."""

    corner: Corner = "bottom right"
    screen: str | None = None  # the screen's name; None: the one with the menu bar
    core: Core = "jarvis"

    @classmethod
    def load(cls, path: Path | None) -> "Remembered":
        """What was saved; anything missing, unreadable or unknown keeps its default."""
        try:
            saved = json.loads(path.read_text()) if path and path.exists() else {}
        except (OSError, ValueError):
            saved = {}
        remembered = cls()
        if saved.get("corner") in get_args(Corner):
            remembered.corner = saved["corner"]
        if isinstance(saved.get("screen"), str):
            remembered.screen = saved["screen"]
        if saved.get("core") in get_args(Core):
            remembered.core = saved["core"]
        return remembered

    def save(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=2))


def screen_name(screen: AppKit.NSScreen) -> str:
    return str(screen.localizedName())


def find_screen(name: str | None) -> AppKit.NSScreen:
    """The screen with that name if it's plugged in, else the one with the menu bar."""
    screens = list(AppKit.NSScreen.screens())
    return next((screen for screen in screens if screen_name(screen) == name), screens[0])


class PageWatcher(AppKit.NSObject, protocols=[objc.protocolNamed("WKNavigationDelegate")]):
    """Tells the overlay when its page has loaded (WebKit's navigation delegate)."""

    def webView_didFinishNavigation_(self, web_view: Any, navigation: Any) -> None:
        self.on_load()


class ScreenWatcher(AppKit.NSObject):
    """Tells the overlay when displays are plugged in, unplugged or rearranged."""

    def screensChanged_(self, notification: Any) -> None:
        self.on_change()


class Overlay:
    """The orb's window. Starts hidden: call `show()`."""

    def __init__(self, page: Path = PAGE, size: float = SIZE, memory: Path | None = None) -> None:
        self.size = size
        self.memory = memory  # where to remember its place and centrepiece (None: nowhere)
        remembered = Remembered.load(memory)
        self.corner: Corner = remembered.corner
        self.screen = find_screen(remembered.screen)
        self.core: Core = remembered.core
        self.latest: dict[str, Any] = {"core": self.core}  # all the page has been told
        self.loaded = False

        frame = Foundation.NSMakeRect(0, 0, size, size)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setHasShadow_(False)
        window.setIgnoresMouseEvents_(True)  # clicks go to whatever is underneath
        window.setLevel_(AppKit.NSStatusWindowLevel)  # above ordinary windows
        window.setCollectionBehavior_(ON_EVERY_DESKTOP)
        window.setReleasedWhenClosed_(False)
        self.window = window

        web = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            frame, WebKit.WKWebViewConfiguration.alloc().init()
        )
        web.setValue_forKey_(False, "drawsBackground")  # the page's clear background shows through
        self.page_watcher = PageWatcher.alloc().init()  # kept: WebKit only holds it weakly
        self.page_watcher.on_load = self.page_loaded
        web.setNavigationDelegate_(self.page_watcher)
        window.setContentView_(web)
        self.web = web
        url = Foundation.NSURL.fileURLWithPath_(str(page))
        web.loadFileURL_allowingReadAccessToURL_(url, url.URLByDeletingLastPathComponent())

        self.screen_watcher = ScreenWatcher.alloc().init()
        self.screen_watcher.on_change = self.screens_changed
        Foundation.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.screen_watcher,
            "screensChanged:",
            AppKit.NSApplicationDidChangeScreenParametersNotification,
            None,
        )
        self.place()

    # --- From any thread ---
    def update(self, **fields: Any) -> None:
        """Show what's happening: any of `state`, `awake` (bool), `level` (0 to 1), `core`."""
        AppHelper.callAfter(self.tell_page, fields)

    async def apply(self, command: DisplayCommand) -> str:
        """Show, hide, move or switch the orb; returns what it did, in words (for Jarvis)."""
        return await on_main_thread(self.carry_out, command)

    # --- Main thread only ---
    def show(self) -> None:
        self.window.orderFrontRegardless()  # shows it without taking your keyboard focus

    def hide(self) -> None:
        self.window.orderOut_(None)

    def place(self, corner: Corner | None = None, screen: AppKit.NSScreen | None = None) -> None:
        self.corner = corner or self.corner
        self.screen = screen or self.screen
        visible = self.screen.visibleFrame()  # without the menu bar and the Dock
        area = (visible.origin.x, visible.origin.y, visible.size.width, visible.size.height)
        self.window.setFrameOrigin_(corner_origin(area, self.size, self.corner))

    def carry_out(self, command: DisplayCommand) -> str:
        if command.action == "hide":
            if not self.window.isVisible():
                return "The orb was already hidden."
            self.hide()
            return "The orb is hidden."
        if command.action == "switch":
            self.core = command.core or "jarvis"
            self.tell_page({"core": self.core})
            self.remember()
            middle = f"{CORE_NAMES[self.core]} in the middle"
            if self.window.isVisible():
                return f"The orb now has {middle}."
            self.show()
            return f"The orb is showing again, with {middle}."
        screen = self.screen
        if command.screen == "other":
            names = [screen_name(s) for s in AppKit.NSScreen.screens()]
            if len(names) < 2:
                return "There's only one screen; the orb stayed where it was."
            here = names.index(screen_name(self.screen)) if screen_name(self.screen) in names else 0
            screen = AppKit.NSScreen.screens()[(here + 1) % len(names)]
        if command.action == "show" and self.window.isVisible() and screen == self.screen:
            return f"The orb was already showing, {self.corner} of the {screen_name(screen)}."
        self.place(command.corner if command.action == "move" else None, screen)
        self.show()
        self.remember()
        return f"The orb is showing, {self.corner} of the {screen_name(screen)}."

    def remember(self) -> None:
        Remembered(self.corner, screen_name(self.screen), self.core).save(self.memory)

    def tell_page(self, fields: dict[str, Any]) -> None:
        self.latest.update(fields)
        if self.loaded:
            script = f"orb.update({json.dumps(fields)})"
            self.web.evaluateJavaScript_completionHandler_(script, None)

    def page_loaded(self) -> None:
        self.loaded = True
        self.tell_page(dict(self.latest))  # catch up on everything said before it was ready

    def screens_changed(self) -> None:
        """A display came or went: stay on the same screen if it's still there, else go to the
        main one."""
        screens = list(AppKit.NSScreen.screens())
        same = [s for s in screens if screen_name(s) == screen_name(self.screen)]
        self.place(screen=same[0] if same else screens[0])


async def on_main_thread[T](work: Callable[..., T], *args: Any) -> T:
    """Run `work` on the main thread (AppKit's) and wait for its result."""
    future: Future[T] = Future()

    def run() -> None:
        try:
            future.set_result(work(*args))
        except BaseException as error:
            future.set_exception(error)

    AppHelper.callAfter(run)
    return await asyncio.wrap_future(future)
