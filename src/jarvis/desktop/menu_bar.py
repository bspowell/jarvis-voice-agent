"""Jarvis's icon in the menu bar, and its menu (what the menu says: menu.py).

An `NSStatusItem`. The icon is drawn here: two rings, like the orb's; a dot in the middle while
he's listening, a slash while your mic is muted, faded while he's off. The menu is rebuilt each
time it opens, so it always says how things are now. Its items carry macOS's own symbols (SF
Symbols), and the first line a coloured dot for how he is. Main thread only.
"""

import logging
from collections.abc import Callable
from typing import Any

import AppKit

from jarvis.desktop.menu import SEPARATOR, Item, Snapshot, icon_look, menu_items, status_line

logger = logging.getLogger(__name__)

ICON_SIZE = 18  # points: the menu bar's usual
LOOKS = ("asleep", "awake", "muted")
SYMBOL_SIZE = 13  # points: the menu's text size
DOT_SIZE = 8  # points: the status line's dot
MODIFIERS = {  # a shortcut's symbols, as macOS's flags
    "⌃": AppKit.NSEventModifierFlagControl,
    "⌥": AppKit.NSEventModifierFlagOption,
    "⇧": AppKit.NSEventModifierFlagShift,
    "⌘": AppKit.NSEventModifierFlagCommand,
}
DOT_COLOURS = {
    "green": AppKit.NSColor.systemGreenColor,
    "orange": AppKit.NSColor.systemOrangeColor,
    "red": AppKit.NSColor.systemRedColor,
    "grey": AppKit.NSColor.systemGrayColor,
}
ON, OFF = AppKit.NSControlStateValueOn, AppKit.NSControlStateValueOff  # an item's tick


class MenuEvents(AppKit.NSObject):
    """Where macOS reports clicks on the menu, and that it's about to open."""

    def clicked_(self, sender: Any) -> None:
        self.on_click(int(sender.tag()))

    def menuNeedsUpdate_(self, menu: Any) -> None:
        self.on_open(menu)


class MenuBar:
    """The icon and its menu. `snapshot` says how things are (`True`: with the accounts, read
    from the Keychain, which only the open menu needs); `actions` does what a click asks (by the
    item's action name, with its argument)."""

    def __init__(
        self,
        snapshot: Callable[[bool], Snapshot],
        actions: dict[str, Callable[[str | None], None]],
    ) -> None:
        self.snapshot = snapshot
        self.actions = actions
        self.clicks: list[tuple[str, str | None]] = []  # by menu item tag: what each one does
        self.icons = {look: ring_icon(look) for look in LOOKS}
        self.images: dict[str, Any] = {}  # symbols and dots, made once each
        self.events = MenuEvents.alloc().init()  # kept: the menu only holds it weakly
        self.events.on_click = self.clicked
        self.events.on_open = self.fill
        self.menu = AppKit.NSMenu.alloc().initWithTitle_("Jarvis")
        self.menu.setAutoenablesItems_(False)  # each item says whether it's enabled
        self.menu.setDelegate_(self.events)
        bar = AppKit.NSStatusBar.systemStatusBar()
        self.item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self.item.setMenu_(self.menu)
        self.refresh()

    def refresh(self) -> None:
        """Redraw the icon (and its tooltip) for how things are now."""
        snapshot = self.snapshot(False)
        button = self.item.button()
        button.setImage_(self.icons[icon_look(snapshot)])
        button.setAppearsDisabled_(snapshot.state != "on")  # faded while he's off
        button.setToolTip_(f"Jarvis: {status_line(snapshot)}")

    def fill(self, menu: Any) -> None:
        """The menu is about to open: write it afresh."""
        menu.removeAllItems()
        self.clicks = []
        for item in menu_items(self.snapshot(True)):
            menu.addItem_(self.make(item))

    def make(self, item: Item) -> Any:
        if item == SEPARATOR:
            return AppKit.NSMenuItem.separatorItem()
        action = "clicked:" if item.action else None
        key, modifiers = key_equivalent(item.shortcut) if item.shortcut else ("", 0)
        made = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            item.title, action, key
        )
        if key:  # shown beside it; hotkeys.py makes ⌃⌥J and ⌃⌥M work in every app
            made.setKeyEquivalentModifierMask_(modifiers)
        if image := self.image_for(item):
            made.setImage_(image)
        if item.action:
            made.setTarget_(self.events)
            made.setTag_(len(self.clicks))
            self.clicks.append((item.action, item.argument))
        made.setEnabled_(item.enabled and bool(item.action or item.submenu))
        made.setState_(ON if item.checked else OFF)
        if item.hint:
            made.setToolTip_(item.hint)
        if item.submenu:
            submenu = AppKit.NSMenu.alloc().initWithTitle_(item.title)
            submenu.setAutoenablesItems_(False)
            for inner in item.submenu:
                submenu.addItem_(self.make(inner))
            made.setSubmenu_(submenu)
        return made

    def image_for(self, item: Item) -> Any:
        """The item's symbol or dot (None: neither, or a symbol this macOS doesn't have)."""
        name = item.icon or (f"dot:{item.dot}" if item.dot else None)
        if name is None:
            return None
        if name not in self.images:
            self.images[name] = dot(item.dot) if item.dot else symbol(name)
        return self.images[name]

    def clicked(self, tag: int) -> None:
        action, argument = self.clicks[tag]
        try:
            self.actions[action](argument)
        except Exception:  # a menu click must never take the app down
            logger.exception("The menu's %r failed.", action)

    def close(self) -> None:
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)


def key_equivalent(shortcut: str) -> tuple[str, int]:
    """ "⌃⌥J" → ("j", Control | Option): how macOS's menus take a shortcut."""
    *symbols, key = shortcut
    modifiers = 0
    for mark in symbols:
        modifiers |= MODIFIERS[mark]
    return key.lower(), modifiers


def symbol(name: str) -> Any:
    """One of macOS's symbols (SF Symbols), at the menu's text size; None if it has no such."""
    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        logger.warning("No symbol called %r: the menu shows that item without one.", name)
        return None
    size = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        SYMBOL_SIZE, AppKit.NSFontWeightRegular
    )
    return image.imageWithSymbolConfiguration_(size)


def dot(colour: str | None) -> Any:
    """The status line's dot, as wide as a symbol so the titles line up."""
    fill = DOT_COLOURS.get(colour or "grey", AppKit.NSColor.systemGrayColor)()

    def draw(rect: Any) -> bool:
        fill.set()
        inset = (rect.size.width - DOT_SIZE) / 2
        circle = AppKit.NSInsetRect(rect, inset, inset)
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(circle).fill()
        return True

    side = SYMBOL_SIZE + 3
    return AppKit.NSImage.imageWithSize_flipped_drawingHandler_(
        AppKit.NSMakeSize(side, side), False, draw
    )


def ring_icon(look: str) -> Any:
    """The icon: a dotted outer ring and a solid inner one, like the orb's. "awake" adds a dot
    in the middle, "muted" a slash across. A template image: macOS colours it for light and
    dark menu bars."""

    def draw(rect: Any) -> bool:
        AppKit.NSColor.blackColor().set()
        outer = AppKit.NSBezierPath.bezierPathWithOvalInRect_(AppKit.NSInsetRect(rect, 1.5, 1.5))
        outer.setLineWidth_(1.5)
        outer.setLineDash_count_phase_([2.2, 1.6], 2, 0)
        outer.stroke()
        inner = AppKit.NSBezierPath.bezierPathWithOvalInRect_(AppKit.NSInsetRect(rect, 5, 5))
        inner.setLineWidth_(1.5)
        inner.stroke()
        if look == "awake":
            AppKit.NSBezierPath.bezierPathWithOvalInRect_(AppKit.NSInsetRect(rect, 7.4, 7.4)).fill()
        if look == "muted":
            slash = AppKit.NSBezierPath.bezierPath()
            left, bottom = rect.origin.x + 3, rect.origin.y + 3
            slash.moveToPoint_((left, bottom))
            slash.lineToPoint_((left + rect.size.width - 6, bottom + rect.size.height - 6))
            context = AppKit.NSGraphicsContext.currentContext()
            context.setCompositingOperation_(AppKit.NSCompositingOperationClear)  # a gap first
            slash.setLineWidth_(4.0)
            slash.stroke()
            context.setCompositingOperation_(AppKit.NSCompositingOperationSourceOver)
            slash.setLineWidth_(1.6)
            slash.stroke()
        return True

    size = AppKit.NSMakeSize(ICON_SIZE, ICON_SIZE)
    image = AppKit.NSImage.imageWithSize_flipped_drawingHandler_(size, False, draw)
    image.setTemplate_(True)
    image.setAccessibilityDescription_("Jarvis")
    return image
