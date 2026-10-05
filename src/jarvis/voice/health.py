"""Health checks for Jarvis's ears and voice (`jarvis doctor`). They only use httpx, so
the CLI starts quickly: LiveKit itself is only loaded by the voice agent.
"""

import asyncio
from datetime import datetime

import httpx

from jarvis.agents.registry import CHECK_TIMEOUT_SECONDS, Health, health
from jarvis.config import Settings

DEEPGRAM_API = "https://api.deepgram.com/v1"
ELEVENLABS_API = "https://api.elevenlabs.io/v1"


async def check_deepgram(settings: Settings, http: httpx.AsyncClient) -> str:
    """Health check: a status line if Deepgram accepts the key; raises with the reason if not."""
    if settings.deepgram_api_key is None:
        raise RuntimeError("DEEPGRAM_API_KEY is missing from .env.")
    key = settings.deepgram_api_key.get_secret_value()
    response = await http.get(f"{DEEPGRAM_API}/projects", headers={"Authorization": f"Token {key}"})
    if not response.is_success:
        raise RuntimeError(f"Deepgram turned the key down ({response.status_code}).")
    return f"Deepgram ready, model {settings.voice.stt_model}."


async def check_elevenlabs(settings: Settings, http: httpx.AsyncClient) -> str:
    """Health check: the voice's name and the credits left this month; raises if it can't speak."""
    if settings.eleven_api_key is None or not settings.elevenlabs_voice_id:
        raise RuntimeError("ELEVEN_API_KEY or ELEVENLABS_VOICE_ID is missing from .env.")
    headers = {"xi-api-key": settings.eleven_api_key.get_secret_value()}
    voice, plan = await asyncio.gather(
        http.get(f"{ELEVENLABS_API}/voices/{settings.elevenlabs_voice_id}", headers=headers),
        http.get(f"{ELEVENLABS_API}/user/subscription", headers=headers),
    )
    if not voice.is_success:
        raise RuntimeError(f"ElevenLabs can't find the voice ({voice.status_code}).")
    status = f"ElevenLabs ready, voice “{voice.json()['name']}”, model {settings.voice.tts_model}"
    if not plan.is_success:  # a key allowed to speak but not to read the plan still works
        return f"{status}."
    usage = plan.json()
    limit = usage["character_limit"]
    left = limit - usage["character_count"]
    resets = datetime.fromtimestamp(usage["next_character_count_reset_unix"]).astimezone()
    return f"{status}: {left:,} of {limit:,} credits left (resets {resets:%-d %b})."


async def check_voice(settings: Settings) -> dict[str, Health]:
    """Check the ears, the voice and the fallback voice (`jarvis doctor`)."""
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as http:
        ears, mouth = await asyncio.gather(
            check_deepgram(settings, http), check_elevenlabs(settings, http), return_exceptions=True
        )
    try:
        fallback: str | Exception = check_fallback_voice(settings)
    except RuntimeError as problem:
        fallback = problem
    return {
        "Ears (speech to text)": health(ears),
        "Voice (text to speech)": health(mouth),
        "Fallback voice (offline)": health(fallback),
    }


def check_fallback_voice(settings: Settings) -> str:
    """Kokoro is ready once its files are downloaded (the first voice session does that)."""
    from jarvis.voice.kokoro_tts import kokoro_for  # loads onnxruntime: only when asked

    kokoro = kokoro_for(settings)
    if not kokoro.is_downloaded():
        raise RuntimeError("Not downloaded yet: the first voice session fetches it (~190 MB).")
    return f"Kokoro ready, voice {kokoro.voice}: speaks if ElevenLabs fails."
