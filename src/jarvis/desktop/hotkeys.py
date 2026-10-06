"""Keyboard shortcuts that work in every app: ⌃⌥J (talk to Jarvis) and ⌃⌥M (mute your mic).

macOS's Carbon `RegisterEventHotKey`, through ctypes (PyObjC doesn't wrap it). Watching every
key press would need Accessibility permission; this doesn't: macOS tells the app only when one
of its own shortcuts is pressed. (macOS 15+ refuses shortcuts of Option or Shift alone, hence
Control + Option.) Main thread only: the presses arrive through macOS's event loop.
"""

import ctypes
import logging
import struct
from collections.abc import Callable

logger = logging.getLogger(__name__)

carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")


def four_letters(code: str) -> int:
    """Carbon's four-letter codes ("keyb") as the number they stand for."""
    return struct.unpack(">I", code.encode())[0]


class HotKeyID(ctypes.Structure):
    _fields_ = (("signature", ctypes.c_uint32), ("id", ctypes.c_uint32))


class EventTypeSpec(ctypes.Structure):
    _fields_ = (("event_class", ctypes.c_uint32), ("event_kind", ctypes.c_uint32))


HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
VOID, REF = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
carbon.GetApplicationEventTarget.restype = VOID
carbon.InstallEventHandler.argtypes = [
    VOID,
    HANDLER,
    ctypes.c_uint32,
    ctypes.POINTER(EventTypeSpec),
    VOID,
    REF,
]
carbon.RemoveEventHandler.argtypes = [VOID]
carbon.RegisterEventHotKey.argtypes = [
    ctypes.c_uint32,
    ctypes.c_uint32,
    HotKeyID,
    VOID,
    ctypes.c_uint32,
    REF,
]
carbon.UnregisterEventHotKey.argtypes = [VOID]
carbon.GetEventParameter.argtypes = [
    VOID,
    ctypes.c_uint32,
    ctypes.c_uint32,
    VOID,
    ctypes.c_size_t,
    VOID,
    VOID,
]

KEYBOARD, PRESSED = four_letters("keyb"), 5  # kEventClassKeyboard, kEventHotKeyPressed
DIRECT, HOT_KEY_ID = four_letters("----"), four_letters("hkid")  # where the press says which key
SIGNATURE = four_letters("JRVS")  # marks our shortcuts
NOT_OURS = -9874  # eventNotHandledErr: let others have it
CONTROL, OPTION = 1 << 12, 1 << 11  # Carbon's modifier flags
KEY_CODES = {"j": 38, "m": 46}  # where the keys are on the keyboard (kVK_ANSI_J, kVK_ANSI_M)


class HotKeys:
    """Our shortcuts, each ⌃⌥ + a letter. Close them when the app exits."""

    def __init__(self) -> None:
        self.actions: dict[int, Callable[[], None]] = {}
        self.registered: list[ctypes.c_void_p] = []
        self.target = carbon.GetApplicationEventTarget()
        self.handler = HANDLER(self.pressed)  # kept: Carbon holds only a pointer to it
        self.handler_ref = ctypes.c_void_p()
        spec = EventTypeSpec(KEYBOARD, PRESSED)
        status = carbon.InstallEventHandler(
            self.target, self.handler, 1, ctypes.byref(spec), None, ctypes.byref(self.handler_ref)
        )
        if status != 0:
            logger.warning("Keyboard shortcuts are unavailable (Carbon error %d).", status)

    def add(self, letter: str, action: Callable[[], None]) -> bool:
        """⌃⌥ + `letter` runs `action`. False if another app already has that shortcut."""
        number = len(self.actions) + 1
        ref = ctypes.c_void_p()
        status = carbon.RegisterEventHotKey(
            KEY_CODES[letter],
            CONTROL | OPTION,
            HotKeyID(SIGNATURE, number),
            self.target,
            0,
            ctypes.byref(ref),
        )
        if status != 0:
            logger.warning("⌃⌥%s belongs to another app (error %d).", letter.upper(), status)
            return False
        self.actions[number] = action
        self.registered.append(ref)
        return True

    def pressed(self, _call: int, event: int, _data: int) -> int:
        """Carbon calls this when one of our shortcuts is pressed."""
        which = HotKeyID()
        status = carbon.GetEventParameter(
            event, DIRECT, HOT_KEY_ID, None, ctypes.sizeof(which), None, ctypes.byref(which)
        )
        action = self.actions.get(which.id) if which.signature == SIGNATURE else None
        if status != 0 or action is None:
            return NOT_OURS
        try:
            action()
        except Exception:  # never let an error escape into macOS's event handling
            logger.exception("A shortcut failed.")
        return 0

    def close(self) -> None:
        for ref in self.registered:
            carbon.UnregisterEventHotKey(ref)
        self.registered, self.actions = [], {}
        if self.handler_ref:
            carbon.RemoveEventHandler(self.handler_ref)
            self.handler_ref = ctypes.c_void_p()
