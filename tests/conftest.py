import pytest

from jarvis.tools import location


@pytest.fixture(autouse=True)
def fresh_location_caches(monkeypatch):
    """Every test starts with empty caches, as if Jarvis had just started."""
    monkeypatch.setattr(location, "_ip_place", None)
    monkeypatch.setattr(location, "_known_places", {})
