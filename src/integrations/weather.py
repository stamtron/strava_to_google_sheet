"""
Daily weather integration using Open-Meteo for athlete outdoor training.

Provides historical and forecast weather metrics for Athens, Greece (or configured
coordinates): temperature min/max, apparent temperature, precipitation, wind speeds,
and WMO weather conditions. Caches results locally so historical days are preserved
and forecast calls stay lightweight.
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta

import requests

from src.config import (
    ATHLETE_CITY,
    ATHLETE_LATITUDE,
    ATHLETE_LONGITUDE,
    ATHLETE_TIMEZONE,
    WEATHER_CACHE_FILE,
    WEATHER_CACHE_TTL,
)

logger = logging.getLogger(__name__)

# WMO Weather interpretation codes (WW)
# https://open-meteo.com/en/docs
WMO_CODE_MAP = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Depositing rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Moderate drizzle", "🌦️"),
    55: ("Dense drizzle", "🌧️"),
    56: ("Light freezing drizzle", "🌧️"),
    57: ("Dense freezing drizzle", "🌧️"),
    61: ("Slight rain", "🌦️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Light freezing rain", "🌧️"),
    67: ("Heavy freezing rain", "🌧️"),
    71: ("Slight snow fall", "❄️"),
    73: ("Moderate snow fall", "❄️"),
    75: ("Heavy snow fall", "❄️"),
    77: ("Snow grains", "❄️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    85: ("Slight snow showers", "🌨️"),
    86: ("Heavy snow showers", "🌨️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with slight hail", "⛈️"),
    99: ("Thunderstorm with heavy hail", "⛈️"),
}


def decode_wmo_code(code: int | None) -> tuple[str, str]:
    """Return (condition_text, emoji_icon) for a WMO weather code."""
    if code is None:
        return ("Unknown", "🌡️")
    return WMO_CODE_MAP.get(int(code), ("Fair", "⛅"))


def _load_weather_cache() -> dict[str, dict]:
    """Load cached daily weather from disk."""
    if os.path.exists(WEATHER_CACHE_FILE):
        try:
            with open(WEATHER_CACHE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read weather cache: %s", e)
    return {}


def _save_weather_cache(cache: dict[str, dict]) -> None:
    """Save daily weather to disk."""
    try:
        with open(WEATHER_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        logger.warning("Could not write weather cache: %s", e)


def fetch_weather_from_open_meteo(
    lat: float = ATHLETE_LATITUDE,
    lon: float = ATHLETE_LONGITUDE,
    past_days: int = 7,
    forecast_days: int = 7,
    timeout_sec: float = 6.0,
) -> dict:
    """
    Fetch daily weather from Open-Meteo API.

    Returns the raw JSON response dict or raises requests.RequestException.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "apparent_temperature_max",
            "apparent_temperature_min",
            "precipitation_sum",
            "precipitation_probability_max",
            "wind_speed_10m_max",
            "wind_gusts_10m_max",
            "uv_index_max",
        ],
        "timezone": ATHLETE_TIMEZONE,
        "past_days": past_days,
        "forecast_days": forecast_days,
    }
    resp = requests.get(url, params=params, timeout=timeout_sec)
    resp.raise_for_status()
    return resp.json()


def parse_daily_weather_response(api_data: dict) -> dict[str, dict]:
    """Convert Open-Meteo daily response into a date-keyed dictionary."""
    daily = api_data.get("daily", {})
    dates = daily.get("time", [])
    if not dates:
        return {}

    weather_by_date = {}
    now_ts = time.time()

    for idx, date_str in enumerate(dates):
        code = daily.get("weather_code", [None])[idx] if idx < len(daily.get("weather_code", [])) else None
        condition, icon = decode_wmo_code(code)

        entry = {
            "date": date_str,
            "city": ATHLETE_CITY,
            "weather_code": code,
            "condition": condition,
            "icon": icon,
            "temp_max_c": daily.get("temperature_2m_max", [None])[idx] if idx < len(daily.get("temperature_2m_max", [])) else None,
            "temp_min_c": daily.get("temperature_2m_min", [None])[idx] if idx < len(daily.get("temperature_2m_min", [])) else None,
            "apparent_temp_max_c": daily.get("apparent_temperature_max", [None])[idx] if idx < len(daily.get("apparent_temperature_max", [])) else None,
            "apparent_temp_min_c": daily.get("apparent_temperature_min", [None])[idx] if idx < len(daily.get("apparent_temperature_min", [])) else None,
            "precipitation_mm": daily.get("precipitation_sum", [0.0])[idx] if idx < len(daily.get("precipitation_sum", [])) else 0.0,
            "precip_probability_pct": daily.get("precipitation_probability_max", [0])[idx] if idx < len(daily.get("precipitation_probability_max", [])) else 0,
            "wind_speed_max_kmh": daily.get("wind_speed_10m_max", [None])[idx] if idx < len(daily.get("wind_speed_10m_max", [])) else None,
            "wind_gusts_max_kmh": daily.get("wind_gusts_10m_max", [None])[idx] if idx < len(daily.get("wind_gusts_10m_max", [])) else None,
            "uv_index_max": daily.get("uv_index_max", [None])[idx] if idx < len(daily.get("uv_index_max", [])) else None,
            "cached_at": now_ts,
        }
        weather_by_date[date_str] = entry

    return weather_by_date


def get_weather_outlook(
    past_days: int = 3,
    forecast_days: int = 7,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Get sorted weather list (past recent days through forecast days) for Athens.

    Honours WEATHER_CACHE_TTL for today/future dates. Historical dates are cached
    permanently.
    """
    cache = _load_weather_cache()
    today_str = date.today().isoformat()
    now_ts = time.time()

    # Determine if cache is stale for today or near future
    today_entry = cache.get(today_str)
    cache_needs_refresh = (
        force_refresh
        or not today_entry
        or (now_ts - today_entry.get("cached_at", 0)) > WEATHER_CACHE_TTL
    )

    if cache_needs_refresh:
        try:
            raw_data = fetch_weather_from_open_meteo(
                past_days=past_days,
                forecast_days=forecast_days,
            )
            parsed = parse_daily_weather_response(raw_data)
            cache.update(parsed)
            _save_weather_cache(cache)
        except Exception as e:
            logger.warning("Weather fetch failed; serving cached weather if available: %s", e)

    # Filter and sort desired window
    start_d = date.today() - timedelta(days=past_days)
    end_d = date.today() + timedelta(days=forecast_days - 1)

    result = []
    curr = start_d
    while curr <= end_d:
        d_str = curr.isoformat()
        if d_str in cache:
            result.append(cache[d_str])
        else:
            result.append({
                "date": d_str,
                "city": ATHLETE_CITY,
                "condition": "N/A",
                "icon": "🌡️",
                "temp_max_c": None,
                "temp_min_c": None,
                "precipitation_mm": 0.0,
                "precip_probability_pct": 0,
                "wind_speed_max_kmh": None,
            })
        curr += timedelta(days=1)

    return result


def get_weather_for_date(target_date: str | date) -> dict | None:
    """Return weather dict for a specific date (YYYY-MM-DD) if in cache or accessible."""
    if isinstance(target_date, date):
        d_str = target_date.isoformat()
    else:
        d_str = str(target_date)

    cache = _load_weather_cache()
    if d_str in cache:
        return cache[d_str]

    # Try refreshing outlook to populate
    outlook = get_weather_outlook(past_days=7, forecast_days=7)
    for entry in outlook:
        if entry.get("date") == d_str:
            return entry
    return None
