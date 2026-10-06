"""Keyboard shortcuts: registered with macOS (Carbon), and a press reaches its action. The press
is simulated (sent to the app as macOS would); a real one needs your fingers."""

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS shortcuts")

if sys.platform == "darwin":
    from jarvis.desktop import hotkeys
    from jarvis.desktop.hotkeys import (
        DIRECT,
        HOT_KEY_ID,
        KEYBOARD,
        PRESSED,
        SIGNATURE,
        HotKeyID,
        HotKeys,
        carbon,
    )

    carbon.CreateEvent.argtypes = [
        *(ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_double, ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.SetEventParameter.argtypes = [
        *(ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_void_p)
    ]
    carbon.SendEventToEventTarget.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    carbon.ReleaseEvent.argtypes = [ctypes.c_void_p]
    carbon.GetCurrentEventTime.restype = ctypes.c_double


def press(keys: "HotKeys", number: int, signature: int = 0) -> int:
    """Send the app a "shortcut pressed" event, as macOS does when you press it."""
    event = ctypes.c_void_p()
    carbon.CreateEvent(
        None, KEYBOARD, PRESSED, carbon.GetCurrentEventTime(), 0, ctypes.byref(event)
    )
    which = HotKeyID(signature or SIGNATURE, number)
    carbon.SetEventParameter(event, DIRECT, HOT_KEY_ID, ctypes.sizeof(which), ctypes.byref(which))
    status = carbon.SendEventToEventTarget(event, keys.target)
    carbon.ReleaseEvent(event)
    return status


def test_a_shortcut_press_runs_its_action(monkeypatch):
    # F19 and F18: keys no app is likely to use, so the test never steals your shortcuts.
    monkeypatch.setitem(hotkeys.KEY_CODES, "f19", 80)
    monkeypatch.setitem(hotkeys.KEY_CODES, "f18", 79)
    keys = HotKeys()
    pressed: list[str] = []
    try:
        assert keys.add("f19", lambda: pressed.append("talk"))
        assert keys.add("f18", lambda: pressed.append("mute"))
        assert press(keys, 1) == 0
        assert press(keys, 2) == 0
        assert press(keys, 1, signature=12345) != 0  # another app's: not ours to handle
    finally:
        keys.close()
    assert pressed == ["talk", "mute"]


def test_an_action_that_fails_never_reaches_macos(monkeypatch):
    monkeypatch.setitem(hotkeys.KEY_CODES, "f19", 80)
    keys = HotKeys()

    def broken() -> None:
        raise RuntimeError("oops")

    try:
        assert keys.add("f19", broken)
        assert press(keys, 1) == 0  # logged, and handled
    finally:
        keys.close()
