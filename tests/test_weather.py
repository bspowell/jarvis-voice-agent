from fakes import (
    FORECAST,
    FORECASTER,
    GEOCODER,
    IP_LOOKUP,
    NO_RESULTS,
    PARIS_RESULTS,
    VANCOUVER_IP,
    fake_web,
)
from langchain_core.utils.function_calling import convert_to_openai_tool

from jarvis.tools.location import Place
from jarvis.tools.weather import describe, fetch_forecast, get_weather, weather_report

WEB = {IP_LOOKUP: VANCOUVER_IP, GEOCODER: PARIS_RESULTS, FORECASTER: FORECAST}


def test_model_sees_one_optional_argument():
    schema = convert_to_openai_tool(get_weather)["function"]
    assert schema["name"] == "get_weather"
    assert list(schema["parameters"]["properties"]) == ["place"]
    assert "required" not in schema["parameters"]  # no place means "where I am"


async def test_forecast_is_compact_facts():
    seen = []
    facts = await fetch_forecast(fake_web(WEB, seen), Place("Paris", 48.85, 2.35))
    assert facts == {
        "now": {"temp_c": 17, "feels_like_c": 17, "conditions": "overcast", "wind_kmh": 6},
        "today": {
            "high_c": 18,
            "low_c": 12,
            "conditions": "light rain",
            "precipitation_chance_pct": 80,
            "precipitation_mm": 4.2,
        },
    }
    assert seen[0].url.params["timezone"] == "auto"  # "today" is local to the place


async def test_report_for_where_the_user_is():
    report = await weather_report(fake_web(WEB), place=None, home_city="Vancouver, BC")
    assert report["place"] == "Vancouver, British Columbia, CA"
    assert report["located_by"] == "ip_address"
    assert report["now"]["temp_c"] == 17


async def test_report_for_a_named_place():
    report = await weather_report(fake_web(WEB), place="Paris", home_city="Vancouver, BC")
    assert report["place"] == "Paris, Île-de-France, France"
    assert report["located_by"] == "named_by_user"


async def test_unknown_place_is_a_fact_not_a_crash():
    web = fake_web({GEOCODER: NO_RESULTS})
    report = await weather_report(web, place="Xyzzyplace", home_city="Vancouver, BC")
    assert report == {"error": "No place called 'Xyzzyplace' was found."}


def test_unlisted_weather_code_still_reads():
    assert describe(42) == "weather code 42"
