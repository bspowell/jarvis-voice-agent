import pytest

from jarvis.brain.models import make_model
from jarvis.prompts import load_prompt


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def test_opus_is_tuned_for_quick_replies():
    model = make_model("claude-opus-5-5")
    assert model.reasoning_effort == "low"
    assert model.thinking["block_binding"]["prefix_mismatch_behavior"] == "drop_block"
    assert model.temperature is None  # Opus 5.5 rejects sampling parameters


def test_haiku_skips_settings_it_does_not_support():
    model = make_model("claude-haiku-4-5")
    assert model.reasoning_effort is None
    assert model.thinking is None


def test_persona_prompt_is_frozen_text():
    prompt = load_prompt("jarvis")
    assert "butler" in prompt
    assert "{" not in prompt  # no template slots: changing data goes in user messages instead
