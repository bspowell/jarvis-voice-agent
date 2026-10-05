import numpy as np
import pytest
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectionError, tts

from jarvis.voice.kokoro_tts import KokoroTTS


class FakeKokoro:
    """Stands in for the Kokoro model: every request "speaks" 10 ms, with values out of range."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str, str]] = []

    async def create_stream(self, text: str, voice: str, lang: str):
        self.asked.append((text, voice, lang))
        yield np.linspace(-1.5, 1.5, 240, dtype=np.float32), 24000


class BrokenVoice(tts.TTS):
    """ElevenLabs when it's down: every request fails."""

    def __init__(self) -> None:
        capabilities = tts.TTSCapabilities(streaming=False)
        super().__init__(capabilities=capabilities, sample_rate=24000, num_channels=1)

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return BrokenSpeech(tts=self, input_text=text, conn_options=conn_options)


class BrokenSpeech(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        raise APIConnectionError("ElevenLabs is unreachable.")


def ready_kokoro(tmp_path) -> tuple[KokoroTTS, FakeKokoro]:
    kokoro = KokoroTTS(tmp_path, voice="bm_george")
    kokoro.engine = FakeKokoro()  # as if downloaded and loaded
    return kokoro, kokoro.engine


async def frames_of(speech: tts.ChunkedStream) -> list:
    """The audio frames spoken (LiveKit closes each request with a final frame of silence)."""
    async with speech:
        return [chunk.frame async for chunk in speech if not chunk.is_final]


async def test_kokoro_speaks_through_livekits_tts_interface(tmp_path):
    kokoro, engine = ready_kokoro(tmp_path)
    frames = await frames_of(kokoro.synthesize("Very good, sir."))
    samples = np.concatenate([np.frombuffer(frame.data, dtype=np.int16) for frame in frames])
    assert len(samples) == 240 and frames[0].sample_rate == 24000
    assert (samples.min(), samples.max()) == (-32767, 32767)  # loud samples clipped, not wrapped
    assert engine.asked == [("Very good, sir.", "bm_george", "en-gb")]  # a British voice


def test_without_its_files_kokoro_says_so_instead_of_loading(tmp_path):
    with pytest.raises(RuntimeError, match="haven't finished downloading"):
        KokoroTTS(tmp_path, voice="bm_george").load()


async def test_kokoro_takes_over_when_elevenlabs_fails(tmp_path):
    kokoro, engine = ready_kokoro(tmp_path)
    voices = tts.FallbackAdapter([BrokenVoice(), kokoro], max_retry_per_tts=0)
    try:
        frames = await frames_of(voices.synthesize("Rain at four, sir."))
    finally:
        await voices.aclose()
    assert sum(frame.samples_per_channel for frame in frames) == 240  # Kokoro's audio
    assert engine.asked == [("Rain at four, sir.", "bm_george", "en-gb")]
