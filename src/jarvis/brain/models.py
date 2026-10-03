"""Model factory: the one place that knows how to configure each Claude model."""

from typing import Literal

from langchain_anthropic import ChatAnthropic

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Opt-in API features for the newer models (Opus 5.5, Sonnet 5.5):
# - thinking-binding-controls: if older history no longer matches (we added a tool, summarized),
#   stale thinking blocks are dropped instead of the request failing.
# - server-side-fallback: if a safety check wrongly declines, the API retries on a backup model.
MODERN_MODEL_BETAS = ["thinking-binding-controls-2026-08-01", "server-side-fallback-2026-07-01"]


def make_model(name: str, effort: Effort = "low") -> ChatAnthropic:
    """Build a chat model with the settings that suit its family."""
    if name.startswith("claude-haiku"):
        # Haiku 4.5 is fast and cheap, and doesn't support effort or adaptive thinking.
        return ChatAnthropic(model=name)
    return ChatAnthropic(
        model=name,
        effort=effort,  # how hard it thinks: "low" keeps spoken replies snappy
        thinking={"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
        betas=MODERN_MODEL_BETAS,
        model_kwargs={"fallbacks": "default"},
    )
