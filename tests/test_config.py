from pydantic import SecretStr

from jarvis.config import ModelSettings, Settings


def test_model_defaults_match_the_plan():
    models = ModelSettings()
    assert models.jarvis == "claude-sonnet-5"
    assert models.notes == "claude-haiku-4-5"
    assert models.search == "claude-haiku-4-5"  # quick lookups: speed
    assert models.search_deep == "claude-sonnet-5-5"  # digging deeper: judgment


def test_env_var_overrides_jarvis_toml(monkeypatch):
    monkeypatch.setenv("MODELS__JARVIS", "claude-haiku-4-5")
    assert Settings(_env_file=None).models.jarvis == "claude-haiku-4-5"


def test_secrets_stay_hidden_when_printed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    settings = Settings(_env_file=None)
    assert isinstance(settings.anthropic_api_key, SecretStr)
    assert "sk-test-123" not in repr(settings)


def test_empty_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")
    assert Settings(_env_file=None).firecrawl_api_key is None
