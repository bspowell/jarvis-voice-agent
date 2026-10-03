"""The weather tool: conditions now and today's outlook, from Open-Meteo (free, no key)."""

from typing import Any

import httpx
from langchain.tools import tool

from jarvis.config import get_settings
from jarvis.tools.location import Place, find_place, where_user_is

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 5.0

# Open-Meteo reports conditions as WMO weather codes.
WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "light freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "violent showers",
    85: "light snow showers",
    86: "snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


@tool(parse_docstring=True)
async def get_weather(place: str | None = None) -> dict[str, Any]:
    """Get the weather right now and today's forecast, in Celsius.

    Args:
        place: City, optionally with region or country, e.g. "Paris, Texas".
            Leave out for the user's current location.
    """
    async with http_client() as client:
        return await weather_report(client, place, home_city=get_settings().location.home_city)


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


async def weather_report(
    client: httpx.AsyncClient, place: str | None, home_city: str
) -> dict[str, Any]:
    """Compact facts for the model to put into words. A name that isn't found is a fact too."""
    if place:
        found = await find_place(client, place)
        if found is None:
            return {"error": f"No place called {place!r} was found."}
        where, located_by = found, "named_by_user"
    else:
        where, located_by = await where_user_is(client, home_city)
    return {"place": where.name, "located_by": located_by, **await fetch_forecast(client, where)}


async def fetch_forecast(client: httpx.AsyncClient, place: Place) -> dict[str, Any]:
    """Current conditions and today's outlook at a place (Celsius, km/h, mm)."""
    response = await client.get(
        FORECAST_URL,
        params={
            "latitude": place.latitude,
            "longitude": place.longitude,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,precipitation_sum",
            "timezone": "auto",  # "today" means today where the place is
            "forecast_days": 1,
        },
    )
    response.raise_for_status()
    forecast = response.json()
    now, today = forecast["current"], forecast["daily"]
    return {
        "now": {
            "temp_c": round(now["temperature_2m"]),
            "feels_like_c": round(now["apparent_temperature"]),
            "conditions": describe(now["weather_code"]),
            "wind_kmh": round(now["wind_speed_10m"]),
        },
        "today": {
            "high_c": round(today["temperature_2m_max"][0]),
            "low_c": round(today["temperature_2m_min"][0]),
            "conditions": describe(today["weather_code"][0]),
            "precipitation_chance_pct": today["precipitation_probability_max"][0],
            "precipitation_mm": today["precipitation_sum"][0],
        },
    }


def describe(weather_code: int) -> str:
    return WEATHER_CODES.get(weather_code, f"weather code {weather_code}")
