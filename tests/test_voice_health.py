import httpx
import pytest

from jarvis.config import Settings
from jarvis.voice import health
from jarvis.voice.health import check_deepgram, check_elevenlabs, check_fallback_voice, check_voice
from jarvis.voice.kokoro_tts import MODEL_FILE, VOICES_FILE

VOICE_ID = "voice-123"
# Real response shapes, trimmed to the fields Jarvis reads.
JARVIS_VOICE = {"voice_id": VOICE_ID, "name": "Jarvis", "category": "cloned"}
STARTER_PLAN = {
    "tier": "starter",
    "character_count": 362,
    "character_limit": 40000,
    "next_character_count_reset_unix": 1793721193,  # 3 Nov 2026
}


def voice_settings(**overrides) -> Settings:
    keys = {"deepgram_api_key": "dg-key", "eleven_api_key": "el-key"}
    voice = {
        "stt_model": "flux-general-en",
        "tts_model": "eleven_flash_v2_5",
        "fallback_voice": "bm_george",
    }
    return Settings(_env_file=None, elevenlabs_voice_id=VOICE_ID, voice=voice, **keys | overrides)


def fake_api(answers: dict[str, httpx.Response], seen: list[httpx.Request]) -> httpx.AsyncClient:
    """Each URL path gets a canned reply; requests are recorded in `seen`."""

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers[request.url.path]

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


async def test_deepgram_checks_the_key_with_a_free_request():
    seen = []
    http = fake_api({"/v1/projects": httpx.Response(200, json={"projects": []})}, seen)
    assert await check_deepgram(voice_settings(), http) == "Deepgram ready, model flux-general-en."
    assert seen[0].headers["Authorization"] == "Token dg-key"


async def test_a_refused_deepgram_key_says_so():
    http = fake_api({"/v1/projects": httpx.Response(401)}, [])
    with pytest.raises(RuntimeError, match=r"turned the key down \(401\)"):
        await check_deepgram(voice_settings(), http)


async def test_a_missing_deepgram_key_never_calls_out():
    seen = []
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY is missing"):
        await check_deepgram(voice_settings(deepgram_api_key=None), fake_api({}, seen))
    assert seen == []


async def test_elevenlabs_names_the_voice_and_the_credits_left():
    seen = []
    answers = {
        f"/v1/voices/{VOICE_ID}": httpx.Response(200, json=JARVIS_VOICE),
        "/v1/user/subscription": httpx.Response(200, json=STARTER_PLAN),
    }
    status = await check_elevenlabs(voice_settings(), fake_api(answers, seen))
    assert status.startswith("ElevenLabs ready, voice “Jarvis”, model eleven_flash_v2_5")
    assert "39,638 of 40,000 credits left" in status
    assert all(request.headers["xi-api-key"] == "el-key" for request in seen)


async def test_a_key_that_cant_read_the_plan_still_counts_as_ready():
    answers = {
        f"/v1/voices/{VOICE_ID}": httpx.Response(200, json=JARVIS_VOICE),
        "/v1/user/subscription": httpx.Response(401, json={"detail": "missing_permissions"}),
    }
    status = await check_elevenlabs(voice_settings(), fake_api(answers, []))
    assert status == "ElevenLabs ready, voice “Jarvis”, model eleven_flash_v2_5."


async def test_an_unknown_voice_is_not_ready():
    answers = {
        f"/v1/voices/{VOICE_ID}": httpx.Response(404),
        "/v1/user/subscription": httpx.Response(200, json=STARTER_PLAN),
    }
    with pytest.raises(RuntimeError, match=r"can't find the voice \(404\)"):
        await check_elevenlabs(voice_settings(), fake_api(answers, []))


async def test_check_voice_reports_each_part(monkeypatch, tmp_path):
    async def deepgram(settings, http):
        return "Deepgram ready."

    async def elevenlabs(settings, http):
        raise RuntimeError("ElevenLabs can't find the voice (404).")

    monkeypatch.setattr(health, "check_deepgram", deepgram)
    monkeypatch.setattr(health, "check_elevenlabs", elevenlabs)
    ears, mouth, fallback = (await check_voice(voice_settings(data_dir=tmp_path))).values()
    assert ears.present and ears.detail == "Deepgram ready."
    assert not mouth.present and "can't find the voice" in mouth.detail
    assert not fallback.present and "Not downloaded yet" in fallback.detail


def test_the_fallback_voice_is_ready_once_downloaded(tmp_path):
    folder = tmp_path / "voice" / "kokoro"
    folder.mkdir(parents=True)
    for name in (MODEL_FILE, VOICES_FILE):
        (folder / name).write_bytes(b"model")
    status = check_fallback_voice(voice_settings(data_dir=tmp_path))
    assert status == "Kokoro ready, voice bm_george: speaks if ElevenLabs fails."
