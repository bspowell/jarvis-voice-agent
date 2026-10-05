"""Jarvis settings, loaded from two places that are kept apart on purpose.

- `.env`        → secrets (API keys). Never committed.
- `jarvis.toml` → preferences (models, places, timings). Safe to commit.

Environment variables override both, e.g. `MODELS__JARVIS=claude-haiku-4-5 uv run jarvis chat`.
"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ModelSettings(BaseModel):
    """Which Claude model each agent thinks with."""

    jarvis: str = "claude-sonnet-5"
    notes: str = "claude-haiku-4-5"
    calendar: str = "claude-haiku-4-5"
    search: str = "claude-haiku-4-5"  # quick lookups
    search_deep: str = "claude-sonnet-5-5"  # "dig deeper" / "research"
    summarizer: str = "claude-haiku-4-5"
    approvals: str = "claude-haiku-4-5"  # asks "Shall I…?" and reads your answer


class AuthSettings(BaseModel):
    """How long a sign-in (Google, Notion) lasts before Jarvis asks you to confirm it."""

    remember_days: int = Field(default=30, ge=1)


class FilesSettings(BaseModel):
    """The only folders Jarvis may search, read and write in (hidden and secret files never)."""

    folders: list[str] = ["~/Documents", "~/Desktop", "~/Downloads", "~/coding"]


class NotionSettings(BaseModel):
    """How Jarvis works with Notion."""

    open_pages: bool = True  # show each page he reads or writes in the Notion app


class MemorySettings(BaseModel):
    """How Jarvis remembers."""

    new_day_hour: int = Field(default=4, ge=0, le=23)  # today's conversation starts fresh at 4 AM


class LocationSettings(BaseModel):
    """Where Jarvis assumes you are when he can't work it out from your IP address."""

    home_city: str = "Vancouver, BC"


class VoiceSettings(BaseModel):
    """How Jarvis listens and speaks (the voice agent)."""

    stt_model: str = "flux-general-en"  # Deepgram Flux: your words, and when you've finished
    tts_model: str = "eleven_v4_turbo"  # ElevenLabs model for his voice (ELEVENLABS_VOICE_ID)
    fallback_voice: str = "bm_george"  # Kokoro, offline: takes over if ElevenLabs fails
    follow_up_seconds: float = Field(default=6.0, gt=0)  # after he answers: no "Hey Jarvis" needed


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        toml_file=PROJECT_ROOT / "jarvis.toml",
        env_nested_delimiter="__",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Secrets (.env). SecretStr hides the value when printed or logged.
    anthropic_api_key: SecretStr | None = None
    langsmith_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None
    deepgram_api_key: SecretStr | None = None
    eleven_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None
    # Jarvis's own Google sign-in app (a "Desktop app" OAuth client from Google Cloud).
    # Your Google account's tokens never go here: they live in the macOS Keychain.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    # Preferences (jarvis.toml)
    models: ModelSettings = Field(default_factory=ModelSettings)
    location: LocationSettings = Field(default_factory=LocationSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    files: FilesSettings = Field(default_factory=FilesSettings)
    notion: NotionSettings = Field(default_factory=NotionSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    data_dir: Path = PROJECT_ROOT / "data"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier sources win: arguments > env vars > .env > jarvis.toml > defaults above.
        return init_settings, env_settings, dotenv_settings, TomlConfigSettingsSource(settings_cls)


# What each secret unlocks, shown by `jarvis doctor`.
SECRET_PURPOSES = {
    "anthropic_api_key": "Claude models (Phase 1)",
    "langsmith_api_key": "LangSmith tracing (Phase 1)",
    "firecrawl_api_key": "Web search agent (Phase 4)",
    "google_client_id": "Google sign-in for the Calendar agent (Phase 5)",
    "google_client_secret": "Google sign-in for the Calendar agent (Phase 5)",
    "deepgram_api_key": "Voice: speech-to-text (Deepgram Flux)",
    "eleven_api_key": "Voice: Jarvis's ElevenLabs voice",
    "elevenlabs_voice_id": "Voice: which ElevenLabs voice",
}


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process.

    Also exports `.env` into the environment, because SDKs like Anthropic and LangSmith
    read their keys from environment variables directly.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings()
