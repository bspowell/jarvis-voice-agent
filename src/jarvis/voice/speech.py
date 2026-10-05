"""Jarvis's ears and mouth: the speech services the voice agent uses.

- Ears: Deepgram Flux turns your speech into text and hears when you've finished talking.
- Mouth: ElevenLabs speaks in your cloned "Jarvis" voice.
"""

from livekit.plugins import deepgram, elevenlabs

from jarvis.config import Settings

KEYTERMS = ["Jarvis"]  # words Flux should expect: you say his name all the time


def make_ears(settings: Settings) -> deepgram.STTv2:
    """Deepgram Flux: your words, and when you've finished (its own end-of-turn model)."""
    if settings.deepgram_api_key is None:
        raise RuntimeError("DEEPGRAM_API_KEY is missing from .env.")
    return deepgram.STTv2(
        model=settings.voice.stt_model,
        keyterm=KEYTERMS,
        api_key=settings.deepgram_api_key.get_secret_value(),
    )


def make_voice(settings: Settings) -> elevenlabs.TTS:
    """ElevenLabs, speaking in your cloned voice."""
    if settings.eleven_api_key is None or not settings.elevenlabs_voice_id:
        raise RuntimeError("ELEVEN_API_KEY or ELEVENLABS_VOICE_ID is missing from .env.")
    return elevenlabs.TTS(
        voice_id=settings.elevenlabs_voice_id,
        model=settings.voice.tts_model,
        encoding="pcm_24000",  # raw 24 kHz audio: cleaner than the default 32 kbps MP3
        api_key=settings.eleven_api_key.get_secret_value(),
    )
