"""Small audio helpers, shared by the voice agent and the desktop app (so they stay light)."""

import math

import numpy as np
from livekit import rtc


def decibels(frame: rtc.AudioFrame) -> float:
    """How loud a frame is, in dB below full scale (silence: -100)."""
    samples = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32)
    rms = math.sqrt(float(np.mean(samples**2))) if samples.size else 0.0
    return 20 * math.log10(max(rms / 32768, 1e-5))
