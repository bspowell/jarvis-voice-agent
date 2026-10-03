from langchain_core.utils.function_calling import convert_to_openai_tool

from jarvis.brain.memory import open_memory
from jarvis.tools.memory_tools import (
    delete_fact,
    forget_fact,
    list_facts,
    recall_facts,
    remember_fact,
    save_fact,
)


def test_model_sees_only_the_facts_it_must_supply():
    schemas = {
        tool.name: convert_to_openai_tool(tool)["function"]["parameters"]["properties"]
        for tool in (remember_fact, recall_facts, forget_fact)
    }
    # `runtime` (the store) is filled in by LangChain, so it never appears here.
    assert schemas == {
        "remember_fact": {"fact": schemas["remember_fact"]["fact"]},
        "recall_facts": {},
        "forget_fact": {"fact_id": schemas["forget_fact"]["fact_id"]},
    }


async def test_facts_round_trip(memory):
    fact = "The user's friend Ruth lives in Perth."
    saved = await save_fact(memory.store, fact)
    recalled = await list_facts(memory.store)
    assert [(item["id"], item["fact"]) for item in recalled["facts"]] == [(saved["id"], fact)]

    assert await delete_fact(memory.store, saved["id"]) == {"forgotten": fact}
    assert await list_facts(memory.store) == {"facts": []}


async def test_newest_fact_comes_first(memory):
    await save_fact(memory.store, "Takes his tea with milk.")
    await save_fact(memory.store, "Actually, prefers coffee now.")
    newest, oldest = (await list_facts(memory.store))["facts"]
    assert (newest["fact"], oldest["fact"]) == (
        "Actually, prefers coffee now.",
        "Takes his tea with milk.",
    )


async def test_facts_outlive_a_restart(tmp_path):
    memory = await open_memory(tmp_path / "jarvis.db")
    await save_fact(memory.store, "Allergic to bees.")
    await memory.close()

    memory = await open_memory(tmp_path / "jarvis.db")
    recalled = await list_facts(memory.store)
    await memory.close()
    assert recalled["facts"][0]["fact"] == "Allergic to bees."


async def test_unknown_id_is_a_fact_not_a_crash(memory):
    assert await delete_fact(memory.store, "nope") == {"error": "No saved fact has the id 'nope'."}
