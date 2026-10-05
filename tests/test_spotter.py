"""The wake-word spotter with its real models, on short recordings made with Kokoro (16 kHz)."""

import wave
from pathlib import Path

from livekit import rtc

from jarvis.voice.spotter import STOP, WAKE, Spotter

AUDIO = Path(__file__).parent / "audio"


def console_frames(name: str, rate: int = 24000) -> list[rtc.AudioFrame]:
    """A recording as LiveKit's console delivers it: 24 kHz, in 20 ms frames."""
    with wave.open(str(AUDIO / name), "rb") as file:
        audio = file.readframes(file.getnframes())
    resampler = rtc.AudioResampler(16000, rate)
    pieces = resampler.push(rtc.AudioFrame(audio, 16000, 1, len(audio) // 2)) + resampler.flush()
    audio = b"".join(piece.data.tobytes() for piece in pieces)
    size = rate // 50 * 2  # 20 ms of 16-bit samples
    return [
        rtc.AudioFrame(audio[start : start + size], rate, 1, len(audio[start : start + size]) // 2)
        for start in range(0, len(audio), size)
    ]


def words_heard(name: str, rate: int = 24000) -> list[str]:
    spotter = Spotter()
    return [word for frame in console_frames(name, rate) for word in spotter.hear(frame)]


def test_it_hears_hey_jarvis_once():
    assert words_heard("hey_jarvis.wav") == [WAKE]  # it keeps scoring high: reported once


def test_it_hears_stop():
    assert words_heard("stop.wav") == [STOP]


def test_his_own_lines_wake_nothing():
    assert words_heard("his_line.wav") == []


def test_16_khz_frames_need_no_resampling():
    assert words_heard("hey_jarvis.wav", rate=16000) == [WAKE]
