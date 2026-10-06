"""Long-term memory tools: facts Jarvis keeps across days, saved only when you ask.

Facts live in LangGraph's store, a key-value table in `data/jarvis.db` that every day's
conversation can read. Each tool gets the store from `ToolRuntime`, which LangChain fills in
when it runs the tool; the model never sees that argument.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain.tools import ToolRuntime, tool
from langgraph.store.base import BaseStore

from jarvis.brain.context import JarvisContext

FACTS = ("facts",)  # the store's folder ("namespace") for facts about the user
RECALL_LIMIT = 100  # facts are saved only on request, so they stay few


@tool(parse_docstring=True)
async def remember_fact(fact: str, runtime: ToolRuntime[JarvisContext]) -> dict[str, str]:
    """Save a fact about the user for future days. Only when they ask you to remember it.

    Args:
        fact: One self-contained sentence, e.g. "The user's friend Ruth lives in Perth."
    """
    return await save_fact(store_of(runtime), fact)


@tool
async def recall_facts(runtime: ToolRuntime[JarvisContext]) -> dict[str, Any]:
    """List every fact the user asked you to remember, newest first."""
    return await list_facts(store_of(runtime))


@tool(parse_docstring=True)
async def forget_fact(fact_id: str, runtime: ToolRuntime[JarvisContext]) -> dict[str, str]:
    """Delete a saved fact when the user asks you to forget it.

    Args:
        fact_id: The fact's id, from recall_facts.
    """
    return await delete_fact(store_of(runtime), fact_id)


def store_of(runtime: ToolRuntime[JarvisContext]) -> BaseStore:
    if runtime.store is None:  # a wiring bug, not something to explain to the user
        raise RuntimeError("Jarvis was built without a store; pass one to build_jarvis().")
    return runtime.store


async def save_fact(store: BaseStore, fact: str) -> dict[str, str]:
    fact_id = uuid4().hex[:8]  # short, so the model can quote it back to forget_fact
    await store.aput(FACTS, fact_id, {"fact": fact, "saved_at": datetime.now(UTC).isoformat()})
    return {"saved": fact, "id": fact_id}


async def list_facts(store: BaseStore) -> dict[str, Any]:
    items = await store.asearch(FACTS, limit=RECALL_LIMIT)
    # Newest first, by our own timestamp: the store's only counts whole seconds.
    items.sort(key=lambda item: item.value["saved_at"], reverse=True)
    return {
        "facts": [
            {
                "id": item.key,
                "fact": item.value["fact"],
                "saved_on": local_date(item.value["saved_at"]),
            }
            for item in items
        ]
    }


def local_date(timestamp: str) -> str:
    """ "2026-10-03T22:15:00+00:00" → "2026-10-03" in your time zone."""
    return datetime.fromisoformat(timestamp).astimezone().date().isoformat()


async def delete_fact(store: BaseStore, fact_id: str) -> dict[str, str]:
    item = await store.aget(FACTS, fact_id)
    if item is None:  # an expected outcome, so it's a fact for the model, not an error
        return {"error": f"No saved fact has the id {fact_id!r}."}
    await store.adelete(FACTS, fact_id)
    return {"forgotten": item.value["fact"]}
