import httpx
import pytest
from fakes import (
    GEOCODER,
    IP_LOOKUP,
    IP_UNKNOWN,
    LOCAL_GEO_RESULTS,
    NO_RESULTS,
    PARIS_RESULTS,
    VANCOUVER_IP,
    fake_web,
)

from jarvis.tools.location import Place, find_place, place_from_ip, place_label, where_user_is


def test_label_skips_blanks_and_repeats():
    assert place_label("Tokyo", "Tokyo", "Japan") == "Tokyo, Japan"
    assert place_label("Monaco", None, "Monaco") == "Monaco"


async def test_find_place_by_name():
    seen = []
    place = await find_place(fake_web({GEOCODER: PARIS_RESULTS}, seen), "Paris")
    assert place == Place("Paris, Île-de-France, France", 48.85, 2.35)
    assert seen[0].url.params["name"] == "Paris"


async def test_places_are_looked_up_once():
    seen = []
    web = fake_web({GEOCODER: PARIS_RESULTS}, seen)
    await find_place(web, "Paris")
    await find_place(web, "Paris")
    assert len(seen) == 1


async def test_unknown_place_is_none():
    assert await find_place(fake_web({GEOCODER: NO_RESULTS}), "Xyzzyplace") is None


async def test_place_from_ip_uses_its_exact_coordinates():
    place = await place_from_ip(fake_web({IP_LOOKUP: VANCOUVER_IP}))
    assert place == Place("Vancouver, British Columbia, CA", 49.2497, -123.1193)


async def test_ip_lookup_is_cached():
    seen = []
    web = fake_web({IP_LOOKUP: VANCOUVER_IP}, seen)
    first, second = await place_from_ip(web), await place_from_ip(web)
    assert first == second
    assert len(seen) == 1  # the second answer came from the cache


async def test_ip_location_comes_first():
    seen = []
    web = fake_web({IP_LOOKUP: VANCOUVER_IP, GEOCODER: LOCAL_GEO_RESULTS}, seen)
    place, located_by = await where_user_is(web, home_city="Vancouver, BC")
    assert place.name == "Vancouver, British Columbia, CA"
    assert located_by == "ip_address"
    assert len(seen) == 1  # the home city wasn't needed


@pytest.mark.parametrize(
    "ip_answer", [IP_UNKNOWN, httpx.Response(429), httpx.ConnectError("offline")]
)
async def test_ip_trouble_falls_back_to_home_city(ip_answer):
    web = fake_web({IP_LOOKUP: ip_answer, GEOCODER: LOCAL_GEO_RESULTS})
    place, located_by = await where_user_is(web, home_city="Vancouver, BC")
    assert place.name == "Vancouver, British Columbia, Canada"
    assert located_by == "home_city_fallback"


async def test_home_city_typo_is_reported():
    web = fake_web({IP_LOOKUP: IP_UNKNOWN, GEOCODER: NO_RESULTS})
    with pytest.raises(LookupError, match="Vancouverr"):
        await where_user_is(web, home_city="Vancouverr")
