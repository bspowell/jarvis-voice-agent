import keyring
import pytest
from fakes import MemoryKeychain

from jarvis.brain.memory import open_memory
from jarvis.tools import location


@pytest.fixture(autouse=True)
def no_real_firecrawl_key(monkeypatch):
    """Tests never search the real web (or spend credits), even once .env has a Firecrawl key."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def no_real_google_app(monkeypatch):
    """Tests never sign in to the real Google, even once .env has Jarvis's Google app."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)


@pytest.fixture(autouse=True)
def keychain():
    """Tests never read or write your real Keychain: sign-ins go to a dictionary instead."""
    real = keyring.get_keyring()
    fake = MemoryKeychain()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(real)


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
