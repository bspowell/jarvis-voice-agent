"""Writes by sub-agents: each one pauses for your OK, then the report carries a receipt.

Shared by every sub-agent that can change things (Calendar, Notes):
- `needs_approval`: the `HumanInTheLoopMiddleware` setting for one write tool.
- `with_receipts`: the agent's report, plus facts taken from its tool results.
"""

from collections.abc import Callable, Sequence

from langchain.agents.middleware import InterruptOnConfig, ToolCallRequest
from langchain_core.messages import AnyMessage, ToolMessage

from jarvis.brain.approvals import requested_change, said_no


def needs_approval(
    describe: Callable[..., str], when: Callable[[ToolCallRequest], bool] | None = None
) -> InterruptOnConfig:
    """Pause before the tool runs. You approve or reject; a change is a reject with a reason.

    `when` may skip asking, but only for calls the tool itself will refuse (nothing runs unasked).
    """
    config = InterruptOnConfig(allowed_decisions=["approve", "reject"], description=describe)
    if when is not None:
        config["when"] = when
    return config


def with_receipts(
    messages: Sequence[AnyMessage],
    receipt_in: Callable[[AnyMessage], str | None],
    saved_in: str,
) -> str:
    """The agent's report, plus facts taken from its tool results rather than its prose.

    Jarvis never hears your answer to "Shall I…?" ("make it 1 PM", "no"): that exchange happens
    outside his conversation, and the report's wording can be ambiguous. So code adds what you
    changed or declined while approving, and what was actually saved (`receipt_in` reads it
    from one tool result).
    """
    rejections = [m.text for m in messages if isinstance(m, ToolMessage) and m.status == "error"]
    asked = [change for text in rejections if (change := requested_change(text))]
    saved = [receipt for m in messages if (receipt := receipt_in(m))]
    facts = [messages[-1].text]
    if asked:
        facts.append(f"While approving, the user asked for: {'; '.join(asked)}.")
    if any(said_no(text) for text in rejections) and not saved:
        facts.append("While approving, the user said no, so nothing was saved.")
    if saved:
        facts.append(f"{saved_in}: {'; '.join(saved)}.")
    return "\n".join(facts)
