import pytest

from jarvis.brain.memory import open_memory
from jarvis.tools import location


@pytest.fixture(autouse=True)
def fresh_location_caches(monkeypatch):
    """Every test starts with empty caches, as if Jarvis had just started."""
    monkeypatch.setattr(location, "_ip_place", None)
    monkeypatch.setattr(location, "_known_places", {})


@pytest.fixture
async def memory(tmp_path):
    """A real memory file in a temporary folder, standing in for data/jarvis.db."""
    memory = await open_memory(tmp_path / "jarvis.db")
    yield memory
    await memory.close()
