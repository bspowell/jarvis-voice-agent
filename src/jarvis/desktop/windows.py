"""What the orb's window and the cards' panel share: see-through, above every window, on every
desktop (fullscreen apps too), holding a web page that says when it has loaded.

AppKit + WebKit, via PyObjC. Main thread only.
"""

from pathlib import Path
from typing import Any

import AppKit
import Foundation
import objc

ON_EVERY_DESKTOP = (
    AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces  # every desktop (Space)
    | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary  # over fullscreen apps too
    | AppKit.NSWindowCollectionBehaviorStationary  # stays put when you swipe between desktops
    | AppKit.NSWindowCollectionBehaviorIgnoresCycle  # never a target for Cmd-`
)


def float_above_everything(window: AppKit.NSWindow) -> None:
    """See-through and click-through, above ordinary windows, on every desktop."""
    window.setOpaque_(False)
    window.setBackgroundColor_(AppKit.NSColor.clearColor())
    window.setHasShadow_(False)
    window.setIgnoresMouseEvents_(True)  # clicks go to whatever is underneath
    window.setLevel_(AppKit.NSStatusWindowLevel)  # above ordinary windows
    window.setCollectionBehavior_(ON_EVERY_DESKTOP)
    window.setReleasedWhenClosed_(False)


def load_page(web: Any, page: Path) -> None:
    """Load a page from disk; it may read the files beside it (its scripts and styles)."""
    url = Foundation.NSURL.fileURLWithPath_(str(page))
    web.setValue_forKey_(False, "drawsBackground")  # the page's clear background shows through
    web.loadFileURL_allowingReadAccessToURL_(url, url.URLByDeletingLastPathComponent())


class PageWatcher(AppKit.NSObject, protocols=[objc.protocolNamed("WKNavigationDelegate")]):
    """Tells its owner when its page has loaded (WebKit's navigation delegate). Keep a reference:
    WebKit only holds it weakly."""

    def webView_didFinishNavigation_(self, web_view: Any, navigation: Any) -> None:
        self.on_load()
