"""Kokoro: the offline voice that takes over if ElevenLabs fails (no internet, out of credits).

A small open-source model that runs on your Mac's own processor: free, private, but a different
voice. Its two files (~190 MB) download once, when a voice session first starts.
"""

import asyncio
import logging
import threading
from pathlib import Path

import httpx
import numpy as np
import onnxruntime
from kokoro_onnx import SAMPLE_RATE, Kokoro
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, tts, utils

from jarvis.config import Settings

logger = logging.getLogger(__name__)
onnxruntime.set_default_logger_severity(3)  # errors only: loading prints harmless warnings

DOWNLOADS = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
MODEL_FILE = "kokoro-v1.0.fp16.onnx"  # half the size of the full model, sounds the same
VOICES_FILE = "voices-v1.0.bin"
LANGUAGE = "en-gb"  # how to pronounce English: the British voices (b…) want British English


def kokoro_for(settings: Settings) -> "KokoroTTS":
    """Jarvis's fallback voice, with its files in data/voice/kokoro/."""
    return KokoroTTS(settings.data_dir / "voice" / "kokoro", settings.voice.fallback_voice)


class KokoroTTS(tts.TTS):
    """LiveKit's TTS interface around Kokoro: one sentence in, his (fallback) voice out."""

    def __init__(self, folder: Path, voice: str) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),  # fed one sentence at a time
            sample_rate=SAMPLE_RATE,  # 24 kHz, like ElevenLabs' pcm_24000: no resampling
            num_channels=1,
        )
        self.folder = folder
        self.voice = voice  # e.g. "bm_george": British male
        self.engine: Kokoro | None = None  # loaded once (about 2 seconds), then kept
        self.loading = threading.Lock()  # two loads at once would build two engines

    @property
    def model(self) -> str:
        return MODEL_FILE

    @property
    def provider(self) -> str:
        return "kokoro (offline)"

    def is_downloaded(self) -> bool:
        return all((self.folder / name).exists() for name in (MODEL_FILE, VOICES_FILE))

    async def download(self) -> None:
        """Fetch the model files once (~190 MB). A half-finished download is never used."""
        self.folder.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as http:
            for name in (MODEL_FILE, VOICES_FILE):
                target = self.folder / name
                if target.exists():
                    continue
                partial = target.with_suffix(".partial")
                async with http.stream("GET", f"{DOWNLOADS}/{name}") as response:
                    response.raise_for_status()
                    with partial.open("wb") as file:
                        async for chunk in response.aiter_bytes():
                            file.write(chunk)
                partial.replace(target)
        logger.info("Kokoro, the offline fallback voice, is downloaded and ready.")

    def load(self) -> Kokoro:
        """The model, loaded the first time it's needed (blocks: call it on a thread)."""
        with self.loading:
            if self.engine is None:
                if not self.is_downloaded():
                    raise RuntimeError("Kokoro's model files haven't finished downloading yet.")
                self.engine = Kokoro(str(self.folder / MODEL_FILE), str(self.folder / VOICES_FILE))
            return self.engine

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "KokoroSpeech":
        return KokoroSpeech(tts=self, input_text=text, conn_options=conn_options)


class KokoroSpeech(tts.ChunkedStream):
    """One piece of text spoken by Kokoro, handed to LiveKit as it's made."""

    def __init__(self, *, tts: KokoroTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self.kokoro = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        engine = await asyncio.to_thread(self.kokoro.load)  # never block the event loop
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )
        speech = engine.create_stream(self.input_text, voice=self.kokoro.voice, lang=LANGUAGE)
        async for samples, _ in speech:
            output_emitter.push(pcm16(samples))
        output_emitter.flush()


def pcm16(samples: np.ndarray) -> bytes:
    """Kokoro's float samples (-1 to 1) as the 16-bit audio LiveKit plays."""
    return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
