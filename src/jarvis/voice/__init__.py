"""Voice: Jarvis's ears and mouth (LiveKit). It reaches the Brain only through `Brain.turn()`."""

import os

# onnxruntime (Kokoro's engine) sends usage data to Microsoft by default, and its uploader thread
# can crash Python as it exits. Off before any model loads (it's read when the first one does).
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
