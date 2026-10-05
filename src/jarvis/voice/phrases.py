"""Fixed lines in Jarvis's voice, recorded once and replayed from disk.

"Right away, sir." and the boot greeting never change, so there's no reason to pay ElevenLabs, or
wait for it, every time he says them. The first time a line is needed it's recorded in his voice
and saved in data/voice/phrases/; from then on it plays instantly, for free.
"""

import hashlib
import logging
import wave
from collections.abc import AsyncIterable, AsyncIterator, Collection
from pathlib import Path

from livekit import rtc
from livekit.agents import tts

logger = logging.getLogger(__name__)

FRAME_MS = 100  # recordings play back in 100 ms frames, like live speech


class Phrases:
    """Recordings of fixed lines, one WAV file per line and voice."""

    def __init__(self, folder: Path, voice: tts.TTS, voice_name: str) -> None:
        self.folder = folder
        self.voice = voice  # his main voice only: a fallback voice is never recorded
        self.voice_name = voice_name  # e.g. "<voice id>/eleven_flash_v2_5": a new voice re-records

    def path(self, line: str) -> Path:
        digest = hashlib.sha256(f"{self.voice_name}\n{line}".encode()).hexdigest()[:16]
        return self.folder / f"{digest}.wav"

    def recorded(self, lines: Collection[str]) -> set[str]:
        """Which of `lines` have a recording."""
        return {line for line in lines if self.path(line).exists()}

    async def record(self, lines: Collection[str]) -> None:
        """Record each line that has no recording yet (one request each). Failures only log:
        an unrecorded line is simply spoken live."""
        for line in lines:
            if self.path(line).exists():
                continue
            try:
                async with self.voice.synthesize(line) as speech:
                    frames = [chunk.frame async for chunk in speech]
            except Exception as error:
                logger.warning("Couldn't record %r: %s", line, error)
                continue
            self._save(line, rtc.combine_audio_frames(frames))

    def _save(self, line: str, audio: rtc.AudioFrame) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.path(line).with_suffix(".partial")
        with wave.open(str(partial), "wb") as file:
            file.setnchannels(audio.num_channels)
            file.setsampwidth(2)  # 16-bit samples
            file.setframerate(audio.sample_rate)
            file.writeframes(audio.data.tobytes())
        partial.replace(self.path(line))  # a half-written file never counts as a recording

    async def play(self, line: str) -> AsyncIterator[rtc.AudioFrame]:
        """A recorded line, frame by frame."""
        with wave.open(str(self.path(line)), "rb") as file:
            rate, channels = file.getframerate(), file.getnchannels()
            while data := file.readframes(rate * FRAME_MS // 1000):
                yield rtc.AudioFrame(data, rate, channels, len(data) // (2 * channels))


async def fixed_line(
    text: AsyncIterable[str], lines: Collection[str]
) -> tuple[str | None, AsyncIterable[str]]:
    """Read just enough of `text` to tell whether all of it is one of `lines`.

    Returns `(line, nothing)` if it is. Otherwise `(None, text)`: the same text, unchanged and still
    streaming. A normal reply stops matching within a word or two, so it isn't held back.
    """
    stream = aiter(text)
    read: list[str] = []
    async for chunk in stream:
        read.append(chunk)
        so_far = "".join(read).strip()
        if not any(line.startswith(so_far) for line in lines):
            break  # can't be a fixed line: pass it all on
    else:
        whole = "".join(read).strip()
        if whole in lines:
            return whole, _replay([], stream)
    return None, _replay(read, stream)


async def _replay(read: list[str], rest: AsyncIterator[str]) -> AsyncIterator[str]:
    for chunk in read:
        yield chunk
    async for chunk in rest:
        yield chunk
