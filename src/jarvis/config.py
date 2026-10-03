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
    search: str = "claude-sonnet-5-5"
    summarizer: str = "claude-haiku-4-5"


class MemorySettings(BaseModel):
    """How Jarvis remembers."""

    new_day_hour: int = Field(default=4, ge=0, le=23)  # today's conversation starts fresh at 4 AM


class LocationSettings(BaseModel):
    """Where Jarvis assumes you are when he can't work it out from your IP address."""

    home_city: str = "Vancouver, BC"


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

    # Preferences (jarvis.toml)
    models: ModelSettings = Field(default_factory=ModelSettings)
    location: LocationSettings = Field(default_factory=LocationSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
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
    "deepgram_api_key": "Speech-to-text (Phase 7)",
    "eleven_api_key": "Jarvis's ElevenLabs voice (Phase 7)",
    "elevenlabs_voice_id": "Which ElevenLabs voice (Phase 7)",
}


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process.

    Also exports `.env` into the environment, because SDKs like Anthropic and LangSmith
    read their keys from environment variables directly.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings()
