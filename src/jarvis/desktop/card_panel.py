"""The cards beside the orb: a see-through panel that takes clicks only on a card's links and
buttons, and never takes your keyboard.

- A "non-activating" `NSPanel`: clicking it doesn't bring this app to the front, and it never
  becomes the key window, so your typing stays where it was.
- The page (web/cards.html) draws the cards and says where its links and buttons are. macOS has
  no "click-through except here" for a window, so while a card has something to click, the panel
  watches the pointer (30 times a second): over a link or button it takes clicks, anywhere else
  they go through to the window underneath, as with the orb.
- Clicks come back through WebKit's message handler: a link opens in your browser; "Sign in"
  goes to Jarvis (the room client asks him over LiveKit).

Main thread only, like the orb's window.
"""

import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import AppKit
import Foundation
import objc
import WebKit

from jarvis.desktop.cards import card_for
from jarvis.desktop.windows import PageWatcher, float_above_everything, load_page
from jarvis.display import Corner
from jarvis.events import Event

logger = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "web" / "cards.html"
WIDTH = 320  # the cards are 288 points wide; the rest is room for their glow
HEIGHT = 1000  # at most: a shorter screen gets its own height (the panel is see-through)
TUCK = 24  # how far the panel tucks under the orb's window (the ring sits well inside it)
MARGIN = 12  # from the screen's edges, as the orb keeps
POINTER_SECONDS = 1 / 30  # how often the pointer is checked, while there's something to click
STYLE = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
OPENABLE = ("https://", "http://")  # links from the web open in the browser; nothing else does

Box = tuple[float, float, float, float]  # x, y, width, height (macOS: y counts up from the bottom)


def beside(orb: Box, corner: Corner, area: Box) -> Box:
    """Where the panel goes: beside the orb's window, on the side away from the screen's edge,
    lined up with the orb's bottom (or its top, in a top corner). `area`: the screen's usable
    part."""
    orb_x, orb_y, orb_width, orb_height = orb
    height = min(HEIGHT, area[3] - 2 * MARGIN)
    x = orb_x - WIDTH + TUCK if corner.endswith("right") else orb_x + orb_width - TUCK
    y = orb_y if corner.startswith("bottom") else orb_y + orb_height - height
    return x, y, WIDTH, height


def spot_under(hotspots: Sequence[dict[str, Any]], x: float, y: float) -> str | None:
    """The link or button at a point on the page (from its top left), if any."""
    for spot in hotspots:
        if spot["x"] <= x <= spot["x"] + spot["w"] and spot["y"] <= y <= spot["y"] + spot["h"]:
            return str(spot["id"])
    return None


class ClickableWebView(WebKit.WKWebView):
    """A web view whose first click counts. A click on a window that isn't active normally only
    activates it; this panel never is active, so every click would be lost."""

    def acceptsFirstMouse_(self, event: Any) -> bool:
        return True


class PageMessages(AppKit.NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
    """What the page says: `window.webkit.messageHandlers.jarvis.postMessage(json)`."""

    def userContentController_didReceiveScriptMessage_(self, controller: Any, message: Any) -> None:
        self.on_message(json.loads(str(message.body())))


class CardPanel:
    """The cards' panel. Starts hidden: the orb's window shows it, places it, and passes on
    what's happening."""

    def __init__(self, page: Path = PAGE) -> None:
        self.on_sign_in: Callable[[str], None] = lambda service: None  # you clicked "Sign in"
        self.showing: list[str] = []  # the cards on the page, by id
        self.hotspots: list[dict[str, Any]] = []  # where their links and buttons are
        self.hovered: str | None = None
        self.taking_clicks = False
        self.pointer_timer: Any = None  # runs while there's something to click
        self.with_orb = False  # is the orb showing? (hidden, its cards go too)
        self.loaded = False
        self.scripts: list[str] = []  # for the page, once it has loaded
        self.latest: dict[str, dict[str, Any]] = {}  # where it is, the conversation: for a reload

        frame = Foundation.NSMakeRect(0, 0, WIDTH, HEIGHT)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, STYLE, AppKit.NSBackingStoreBuffered, False
        )
        float_above_everything(panel)  # click-through until the pointer is over a link
        panel.setHidesOnDeactivate_(False)  # panels normally hide when their app isn't active
        self.panel = panel

        config = WebKit.WKWebViewConfiguration.alloc().init()
        self.messages = PageMessages.alloc().init()  # kept: the page's messages arrive here
        self.messages.on_message = self.on_message
        config.userContentController().addScriptMessageHandler_name_(self.messages, "jarvis")
        web = ClickableWebView.alloc().initWithFrame_configuration_(frame, config)
        self.page_watcher = PageWatcher.alloc().init()  # kept: WebKit only holds it weakly
        self.page_watcher.on_load = self.page_loaded
        web.setNavigationDelegate_(self.page_watcher)
        panel.setContentView_(web)
        self.web = web
        load_page(web, page)

    # --- What the orb's window tells it ---
    def show(self) -> None:
        self.with_orb = True
        self.panel.orderFrontRegardless()  # shown without taking your focus

    def hide(self) -> None:
        """The orb was hidden ("hide the orb"): its cards go with it."""
        self.with_orb = False
        self.call("cards.clear")
        self.panel.orderOut_(None)
        self.stop_watching_pointer()

    def place(self, orb: Box, corner: Corner, area: Box) -> None:
        x, y, width, height = beside(orb, corner, area)
        self.panel.setFrame_display_(Foundation.NSMakeRect(x, y, width, height), True)
        side = "right" if corner.endswith("right") else "left"  # where the orb is, from the cards
        align = "bottom" if corner.startswith("bottom") else "top"
        self.call("cards.place", {"side": side, "align": align}, remember="place")

    def update(self, fields: dict[str, Any]) -> None:
        """The conversation: whether he's awake, his state ("away": he has left)."""
        self.call("cards.update", fields, remember="update")

    def show_event(self, event: Event) -> None:
        card = card_for(event, datetime.now())
        if card is None or (not self.with_orb and card["kind"] != "sign_in"):
            return  # the orb is hidden, so are its cards (but a sign-in needs its button)
        if not self.panel.isVisible():
            self.panel.orderFrontRegardless()
        self.call("cards.put", card)

    def clear(self) -> str:
        """Every card goes; says how many did (for Jarvis)."""
        count = len(self.showing)
        self.call("cards.clear")
        if count == 0:
            return "There were no cards to clear."
        return f"Cleared {count} card{'' if count == 1 else 's'}."

    # --- The page ---
    def call(self, function: str, argument: Any = None, remember: str | None = None) -> None:
        script = f"{function}({json.dumps(argument) if argument is not None else ''})"
        if remember is not None:
            self.latest[remember] = {**self.latest.get(remember, {}), **(argument or {})}
        if self.loaded:
            self.web.evaluateJavaScript_completionHandler_(script, None)
        elif remember is None:
            self.scripts.append(script)  # a card or a clear: in order, once it has loaded

    def page_loaded(self) -> None:
        self.loaded = True
        for function, argument in (("cards.place", "place"), ("cards.update", "update")):
            if argument in self.latest:
                self.call(function, self.latest[argument])
        for script in self.scripts:
            self.web.evaluateJavaScript_completionHandler_(script, None)
        self.scripts = []

    def on_message(self, message: dict[str, Any]) -> None:
        if message.get("type") == "layout":
            self.showing = list(message.get("cards", []))
            self.hotspots = list(message.get("hotspots", []))
            if self.hotspots:
                self.watch_pointer()
            else:
                self.stop_watching_pointer()
        elif message.get("type") == "click":
            self.clicked(message)

    def clicked(self, message: dict[str, Any]) -> None:
        if message.get("action") == "open":
            url = str(message.get("url", ""))
            if url.startswith(OPENABLE):
                AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.URLWithString_(url))
        elif message.get("action") == "sign_in":
            self.on_sign_in(str(message.get("service", "")))

    # --- Clicks only where there's something to click ---
    def watch_pointer(self) -> None:
        if self.pointer_timer is None:
            self.pointer_timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                POINTER_SECONDS, True, lambda timer: self.follow_pointer()
            )

    def stop_watching_pointer(self) -> None:
        if self.pointer_timer is not None:
            self.pointer_timer.invalidate()
            self.pointer_timer = None
        self.take_clicks(None)

    def follow_pointer(self) -> None:
        pointer, frame = AppKit.NSEvent.mouseLocation(), self.panel.frame()
        x = pointer.x - frame.origin.x
        y = frame.size.height - (pointer.y - frame.origin.y)  # the page counts from its top
        self.take_clicks(spot_under(self.hotspots, x, y) if self.panel.isVisible() else None)

    def take_clicks(self, spot: str | None) -> None:
        """Over a link or button: the panel takes the click. Anywhere else: it goes through."""
        if (spot is not None) != self.taking_clicks:
            self.taking_clicks = spot is not None
            self.panel.setIgnoresMouseEvents_(not self.taking_clicks)
        if spot != self.hovered:
            self.hovered = spot
            self.call("cards.hover", spot)
