"""Jarvis, the orchestrator agent: the personality you talk to."""

from langchain.agents import create_agent
from langchain.agents.middleware import ToolErrorMiddleware
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from jarvis.agents.registry import agent_tools
from jarvis.brain.approvals import Approvals
from jarvis.brain.brain import Brain
from jarvis.brain.context import JarvisContext
from jarvis.brain.memory import Memory, RepairInterruptedToolCalls, summarize_long_days
from jarvis.brain.models import make_model
from jarvis.config import Settings
from jarvis.prompts import load_prompt
from jarvis.tools.display import display_orb
from jarvis.tools.errors import explain_tool_error
from jarvis.tools.memory_tools import forget_fact, recall_facts, remember_fact
from jarvis.tools.weather import get_weather


def build_jarvis(
    settings: Settings,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph:
    """Assemble Jarvis: model + personality + tools + sub-agents + memory."""
    return create_agent(
        model=make_model(settings.models.jarvis),
        # His own quick tools, then one tool per sub-agent (e.g. search_agent).
        tools=[
            *(get_weather, remember_fact, recall_facts, forget_fact, display_orb),
            *agent_tools(settings),
        ],
        system_prompt=load_prompt("jarvis"),
        context_schema=JarvisContext,  # what each run hands his tools (e.g. the orb's controls)
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


def make_brain(settings: Settings, memory: Memory) -> Brain:
    """Jarvis behind the Brain doorway, as text chat and the voice agent both use him."""
    jarvis = build_jarvis(settings, checkpointer=memory.checkpointer, store=memory.store)
    approvals = Approvals(make_model(settings.models.approvals))
    return Brain(jarvis, new_day_hour=settings.memory.new_day_hour, approvals=approvals)
