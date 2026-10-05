"""Wake words: tiny models on your Mac that hear "Hey Jarvis" and "stop" (free, offline).

- "Hey Jarvis": openWakeWord's model (pyopen-wakeword). It often hears a plain "Jarvis" too.
  Its license is non-commercial (CC BY-NC-SA 4.0): fine for personal use; swap it before
  selling an app (microWakeWord's Apache-2.0 "hey jarvis" heard less in our test).
- "Stop": microWakeWord's model, the one Home Assistant uses to stop timers. Apache-2.0, 45 KB,
  kept in wakewords/ (github.com/kahrendt/microWakeWord, release "stop").

Both listen at 16 kHz, so LiveKit's frames (24 kHz from the console) are resampled first.
"""

from pathlib import Path

from livekit import rtc
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import Model, OpenWakeWord, OpenWakeWordFeatures

WAKE = "hey jarvis"
STOP = "stop"

SAMPLE_RATE = 16000  # what both models listen at
CHUNK_BYTES = 1280 * 2  # 80 ms of 16-bit audio: openWakeWord scores in steps of this size
WAKE_THRESHOLD = 0.5  # openWakeWord's score (0 to 1) that counts as "Hey Jarvis"
REST_SECONDS = 1.5  # a word scores high for several chunks: report it once, then rest
STOP_MODEL = Path(__file__).parent / "wakewords" / "stop.json"


class Spotter:
    """Hears every frame from your mic and says which wake words were just spoken."""

    def __init__(self) -> None:
        self.wake_features = OpenWakeWordFeatures.from_builtin()
        self.wake = OpenWakeWord.from_builtin(Model.HEY_JARVIS)
        self.stop_features = MicroWakeWordFeatures()
        self.stop = MicroWakeWord.from_config(STOP_MODEL)
        self.resampler: rtc.AudioResampler | None = None  # made for the first frame's rate
        self.unscored = b""  # 16 kHz audio waiting for a full 80 ms chunk
        self.seconds = 0.0  # audio scored so far: the clock for resting
        self.resting_until = {WAKE: 0.0, STOP: 0.0}

    def hear(self, frame: rtc.AudioFrame) -> set[str]:
        """The wake words heard by the end of this frame (usually none)."""
        self.unscored += self.at_16k(frame)
        heard: set[str] = set()
        while len(self.unscored) >= CHUNK_BYTES:
            chunk, self.unscored = self.unscored[:CHUNK_BYTES], self.unscored[CHUNK_BYTES:]
            self.seconds += CHUNK_BYTES / 2 / SAMPLE_RATE
            heard |= self.score(chunk)
        return heard

    def score(self, chunk: bytes) -> set[str]:
        heard = set()
        for features in self.wake_features.process_streaming(chunk):
            for score in self.wake.process_streaming(features):  # read them all: it keeps state
                if score > WAKE_THRESHOLD:
                    heard.add(WAKE)
        for features in self.stop_features.process_streaming(chunk):
            if self.stop.process_streaming(features):
                heard.add(STOP)
        fresh = {word for word in heard if self.seconds >= self.resting_until[word]}
        for word in fresh:
            self.resting_until[word] = self.seconds + REST_SECONDS
        return fresh

    def at_16k(self, frame: rtc.AudioFrame) -> bytes:
        """The frame's audio at 16 kHz (mono, like LiveKit's mic input)."""
        if frame.sample_rate == SAMPLE_RATE:
            return frame.data.tobytes()
        if self.resampler is None:
            self.resampler = rtc.AudioResampler(frame.sample_rate, SAMPLE_RATE)
        return b"".join(piece.data.tobytes() for piece in self.resampler.push(frame))
