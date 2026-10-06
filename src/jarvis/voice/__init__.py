"""Voice: Jarvis's ears and mouth (LiveKit). It reaches the Brain only through `Brain.turn()`."""

import os
import sys

# onnxruntime (Kokoro's engine) sends usage data to Microsoft by default, and its uploader thread
# can crash Python as it exits. Off before any model loads (it's read when the first one does).
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

AGENT_NAME = "jarvis"  # how the desktop app invites him to a room (LiveKit's "dispatch" name)
# PyJWT warns that the local server's development secret ("secret") is short. It's fixed, public
# and only works on this Mac; a real server's secret is long enough not to trigger it.
SHORT_KEY_WARNING = "The HMAC key is"


def quiet_livekit_exit_noise() -> None:
    """LiveKit's FFI handles fail an `assert` when Python frees them after the FFI has shut down,
    as a process exits: harmless, but a screen of tracebacks every time a session ends."""

    def hook(unraisable: "sys.UnraisableHookArgs") -> None:
        if isinstance(unraisable.exc_value, AssertionError) and "FfiHandle" in repr(
            unraisable.object
        ):
            return
        sys.__unraisablehook__(unraisable)

    sys.unraisablehook = hook
