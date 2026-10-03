"""Where things are: places by name (geocoding) and the user's own spot (IP lookup).

Both services are free and need no key. Jarvis looks up its IP at most once an hour,
well inside ipinfo.io's free allowance.
"""

import time
from dataclasses import dataclass

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
IP_LOOKUP_URL = "https://ipinfo.io/json"
IP_CACHE_SECONDS = 60 * 60  # you rarely change neighbourhood within the hour


@dataclass(frozen=True)
class Place:
    name: str  # e.g. "London, Ontario, Canada"
    latitude: float
    longitude: float


def place_label(*parts: str | None) -> str:
    """Join name parts, skipping blanks and repeats: "Tokyo", "Tokyo", "Japan" → "Tokyo, Japan"."""
    return ", ".join(dict.fromkeys(part for part in parts if part))


_known_places: dict[str, Place] = {}  # places don't move, so each name is looked up once


async def find_place(client: httpx.AsyncClient, name: str) -> Place | None:
    """Look a place up by name, e.g. "Paris" or "Paris, Texas". None if nothing matches."""
    if name in _known_places:
        return _known_places[name]
    response = await client.get(GEOCODING_URL, params={"name": name, "count": 1, "language": "en"})
    response.raise_for_status()
    results = response.json().get("results")
    if not results:
        return None
    best = results[0]
    place = Place(
        name=place_label(best["name"], best.get("admin1"), best.get("country")),
        latitude=best["latitude"],
        longitude=best["longitude"],
    )
    _known_places[name] = place
    return place


_ip_place: tuple[Place, float] | None = None  # last IP lookup: (place, when it was found)


async def place_from_ip(client: httpx.AsyncClient) -> Place | None:
    """Where this computer is, judged by its public IP address. None if unknown.

    Accuracy depends on how the internet provider registered the address: often the right
    neighbourhood, sometimes the next town over.
    """
    global _ip_place
    if _ip_place and time.monotonic() - _ip_place[1] < IP_CACHE_SECONDS:
        return _ip_place[0]
    try:
        response = await client.get(IP_LOOKUP_URL)
        response.raise_for_status()
        found = response.json()
    except httpx.HTTPError:
        return None
    if "loc" not in found:  # private or unknown addresses come back without coordinates
        return None
    latitude, longitude = (float(part) for part in found["loc"].split(","))
    place = Place(
        name=place_label(found.get("city"), found.get("region"), found.get("country")),
        latitude=latitude,
        longitude=longitude,
    )
    _ip_place = (place, time.monotonic())
    return place


async def where_user_is(client: httpx.AsyncClient, home_city: str) -> tuple[Place, str]:
    """The user's place and how we know it: their IP address, or else their home city."""
    if place := await place_from_ip(client):
        return place, "ip_address"
    if place := await find_place(client, home_city):
        return place, "home_city_fallback"
    raise LookupError(f"Home city {home_city!r} wasn't found. Check [location] in jarvis.toml.")
