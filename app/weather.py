"""Current and hourly weather integration backed by Open-Meteo's public API."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import urlopen

from .models import WeatherReport

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
SINGAPORE_LATITUDE = 1.3521
SINGAPORE_LONGITUDE = 103.8198
SINGAPORE_LOCATION_NAME = "Singapore"


class WeatherFetchError(RuntimeError):
    """Raised when Open-Meteo cannot provide a usable weather payload."""


class GeocodingError(RuntimeError):
    """Raised when a place name can't be resolved to coordinates."""


_WMO_CONDITIONS = {
    0: "Clear Sky",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Heavy Drizzle",
    56: "Freezing Drizzle",
    57: "Heavy Freezing Drizzle",
    61: "Light Rain",
    63: "Rain",
    65: "Heavy Rain",
    66: "Freezing Rain",
    67: "Heavy Freezing Rain",
    71: "Light Snow",
    73: "Snow",
    75: "Heavy Snow",
    77: "Snow Grains",
    80: "Rain Showers",
    81: "Heavy Rain Showers",
    82: "Violent Rain Showers",
    85: "Snow Showers",
    86: "Heavy Snow Showers",
    95: "Thunderstorms / Heavy Rain",
    96: "Thunderstorms with Hail",
    99: "Severe Thunderstorms with Hail",
}
_RAINY_CODES = frozenset(
    code for code in _WMO_CONDITIONS if code in {*range(51, 68), *range(80, 83), 95, 96, 99}
)


def wmo_condition(weather_code: int) -> str:
    """Map an Open-Meteo WMO code to an understandable weather condition."""
    return _WMO_CONDITIONS.get(weather_code, "Variable Conditions")


def _thermal_category(temperature: float, humidity: int) -> str:
    if temperature <= 22:
        return "cool_indoor"
    if temperature >= 30 or (temperature >= 27 and humidity >= 75):
        return "hot_humid"
    return "temperate"


def parse_target_datetime(time_str: str) -> datetime:
    """Resolve user time expressions into a UTC-normalized datetime."""
    now = datetime.now(timezone.utc)
    text = (time_str or "").strip().lower()

    if not text or text in ("now", "today", "current"):
        return now

    target_date = now.date()
    if "tomorrow" in text:
        target_date += timedelta(days=1)
    elif "day after tomorrow" in text:
        target_date += timedelta(days=2)

    target_hour = now.hour
    if "morning" in text:
        target_hour = 9
    elif "noon" in text or "lunch" in text:
        target_hour = 12
    elif "afternoon" in text:
        target_hour = 15
    elif "evening" in text or "dinner" in text:
        target_hour = 19
    elif "tonight" in text or "night" in text:
        target_hour = 21
    else:
        match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if match:
            raw_h = int(match.group(1))
            meridiem = match.group(3)
            if meridiem == "pm" and raw_h < 12:
                raw_h += 12
            elif meridiem == "am" and raw_h == 12:
                raw_h = 0
            target_hour = min(max(raw_h, 0), 23)

    return datetime(
        target_date.year, target_date.month, target_date.day, target_hour, tzinfo=timezone.utc
    )


def geocode_location(name: str, *, timeout_seconds: float = 10.0) -> tuple[float, float, str]:
    """Resolve a place name to (latitude, longitude, display_name)."""
    query = urlencode({"name": name.strip(), "count": 1, "language": "en", "format": "json"})
    try:
        with urlopen(f"{OPEN_METEO_GEOCODING_URL}?{query}", timeout=timeout_seconds) as response:
            payload: dict[str, Any] = json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise GeocodingError(f"Unable to look up '{name}': {exc}") from exc

    results = payload.get("results") or []
    if not results:
        raise GeocodingError(f"Couldn't find a location matching '{name}'.")

    top = results[0]
    try:
        latitude = float(top["latitude"])
        longitude = float(top["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodingError(f"Malformed geocoding result for '{name}': {exc}") from exc

    display_parts = [top.get("name"), top.get("admin1"), top.get("country")]
    display_name = ", ".join(part for part in display_parts if part) or name

    return latitude, longitude, display_name


def get_current_weather(
    latitude: float = SINGAPORE_LATITUDE,
    longitude: float = SINGAPORE_LONGITUDE,
    *,
    location_name: str = SINGAPORE_LOCATION_NAME,
    target_time: Optional[datetime] = None,
    timeout_seconds: float = 10.0,
) -> WeatherReport:
    """Fetch conditions for a coordinate pair and target hour."""
    query_params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code",
        "hourly": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code",
        "timezone": "auto",
    }
    url = f"{OPEN_METEO_FORECAST_URL}?{urlencode(query_params)}"

    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload: dict[str, Any] = json.load(response)

        if target_time and "hourly" in payload:
            hourly = payload["hourly"]
            times: list[str] = hourly.get("time", [])
            target_iso_prefix = target_time.strftime("%Y-%m-%dT%H:00")

            matched_idx = 0
            for idx, t in enumerate(times):
                if t.startswith(target_iso_prefix):
                    matched_idx = idx
                    break

            temperature = float(hourly["temperature_2m"][matched_idx])
            humidity = int(hourly["relative_humidity_2m"][matched_idx])
            apparent_temperature = float(hourly["apparent_temperature"][matched_idx])
            precipitation = float(hourly["precipitation"][matched_idx])
            weather_code = int(hourly["weather_code"][matched_idx])
        else:
            current = payload["current"]
            temperature = float(current["temperature_2m"])
            humidity = int(current["relative_humidity_2m"])
            apparent_temperature = float(current["apparent_temperature"])
            precipitation = float(current["precipitation"])
            weather_code = int(current["weather_code"])

    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise WeatherFetchError(f"Unable to fetch weather: {exc}") from exc

    return WeatherReport(
        temperature=temperature,
        humidity=humidity,
        apparent_temperature=apparent_temperature,
        precipitation=precipitation,
        condition=wmo_condition(weather_code),
        is_rainy=weather_code in _RAINY_CODES or precipitation > 0,
        thermal_category=_thermal_category(temperature, humidity),
        location_name=location_name,
    )