"""Jarvis, the orchestrator agent: the personality you talk to."""

from langchain.agents import create_agent
from langchain.agents.middleware import ToolErrorMiddleware
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from jarvis.brain.memory import RepairInterruptedToolCalls, summarize_long_days
from jarvis.brain.models import make_model
from jarvis.config import Settings
from jarvis.prompts import load_prompt
from jarvis.tools.errors import explain_tool_error
from jarvis.tools.memory_tools import forget_fact, recall_facts, remember_fact
from jarvis.tools.weather import get_weather


def build_jarvis(
    settings: Settings,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph:
    """Assemble Jarvis: model + personality + tools + memory."""
    return create_agent(
        model=make_model(settings.models.jarvis),
        tools=[get_weather, remember_fact, recall_facts, forget_fact],
        system_prompt=load_prompt("jarvis"),
        # Middleware wraps each step of the loop, in this order:
        middleware=[
            RepairInterruptedToolCalls(),  # a turn cut off mid-tool can't break the rest of the day
            summarize_long_days(make_model(settings.models.summarizer)),
            # Reuse the unchanged start of every request (tools, prompt, earlier messages).
            # 1 hour, not the default 5 minutes: questions to Jarvis come minutes apart.
            AnthropicPromptCachingMiddleware(ttl="1h", unsupported_model_behavior="ignore"),
            ToolErrorMiddleware(explain_tool_error),  # network failures become notes, not crashes
        ],
        checkpointer=checkpointer,  # today's conversation
        store=store,  # long-term facts
        name="jarvis",
    )
