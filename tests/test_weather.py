"""
Unit tests for the Open-Meteo weather integration module.
"""

import json
import time
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.weather import (
    decode_wmo_code,
    get_weather_for_date,
    get_weather_outlook,
    parse_daily_weather_response,
)


def test_decode_wmo_code():
    assert decode_wmo_code(0) == ("Clear sky", "☀️")
    assert decode_wmo_code(1) == ("Mainly clear", "🌤️")
    assert decode_wmo_code(2) == ("Partly cloudy", "⛅")
    assert decode_wmo_code(3) == ("Overcast", "☁️")
    assert decode_wmo_code(63) == ("Moderate rain", "🌧️")
    assert decode_wmo_code(71) == ("Slight snow fall", "❄️")
    assert decode_wmo_code(95) == ("Thunderstorm", "⛈️")
    assert decode_wmo_code(None) == ("Unknown", "🌡️")
    assert decode_wmo_code(999) == ("Fair", "⛅")


def test_parse_daily_weather_response():
    mock_payload = {
        "daily": {
            "time": ["2026-09-01", "2026-09-02"],
            "weather_code": [0, 61],
            "temperature_2m_max": [31.5, 28.0],
            "temperature_2m_min": [22.0, 20.5],
            "apparent_temperature_max": [33.0, 29.0],
            "apparent_temperature_min": [22.5, 21.0],
            "precipitation_sum": [0.0, 4.2],
            "precipitation_probability_max": [5, 80],
            "wind_speed_10m_max": [18.5, 25.2],
            "wind_gusts_10m_max": [32.0, 45.0],
            "uv_index_max": [7.8, 4.5],
        }
    }

    parsed = parse_daily_weather_response(mock_payload)
    assert "2026-09-01" in parsed
    assert "2026-09-02" in parsed

    day1 = parsed["2026-09-01"]
    assert day1["condition"] == "Clear sky"
    assert day1["icon"] == "☀️"
    assert day1["temp_max_c"] == 31.5
    assert day1["precipitation_mm"] == 0.0
    assert day1["precip_probability_pct"] == 5

    day2 = parsed["2026-09-02"]
    assert day2["condition"] == "Slight rain"
    assert day2["icon"] == "🌦️"
    assert day2["temp_max_c"] == 28.0
    assert day2["precipitation_mm"] == 4.2
    assert day2["precip_probability_pct"] == 80


def test_get_weather_outlook_with_caching(tmp_path, monkeypatch):
    cache_file = tmp_path / ".weather_cache.json"
    monkeypatch.setattr("src.integrations.weather.WEATHER_CACHE_FILE", str(cache_file))

    today_str = date.today().isoformat()
    mock_payload = {
        "daily": {
            "time": [today_str],
            "weather_code": [0],
            "temperature_2m_max": [30.0],
            "temperature_2m_min": [21.0],
            "apparent_temperature_max": [31.0],
            "apparent_temperature_min": [21.0],
            "precipitation_sum": [0.0],
            "precipitation_probability_max": [0],
            "wind_speed_10m_max": [15.0],
            "wind_gusts_10m_max": [25.0],
            "uv_index_max": [7.0],
        }
    }

    mock_get = MagicMock()
    mock_get.return_value.json.return_value = mock_payload
    mock_get.return_value.raise_for_status = MagicMock()

    with patch("requests.get", mock_get):
        outlook = get_weather_outlook(past_days=0, forecast_days=1)
        assert len(outlook) == 1
        assert outlook[0]["date"] == today_str
        assert outlook[0]["temp_max_c"] == 30.0
        assert mock_get.call_count == 1

        # Second call within TTL should read from cache without hitting requests.get
        outlook2 = get_weather_outlook(past_days=0, forecast_days=1)
        assert len(outlook2) == 1
        assert mock_get.call_count == 1  # No additional network call


def test_get_weather_for_date(tmp_path, monkeypatch):
    cache_file = tmp_path / ".weather_cache.json"
    monkeypatch.setattr("src.integrations.weather.WEATHER_CACHE_FILE", str(cache_file))

    target_d = "2026-08-15"
    initial_cache = {
        target_d: {
            "date": target_d,
            "city": "Athens, Greece",
            "condition": "Clear sky",
            "icon": "☀️",
            "temp_max_c": 34.0,
            "temp_min_c": 24.0,
            "precipitation_mm": 0.0,
            "wind_speed_max_kmh": 20.0,
            "cached_at": time.time(),
        }
    }
    with open(cache_file, "w") as f:
        json.dump(initial_cache, f)

    res = get_weather_for_date(target_d)
    assert res is not None
    assert res["temp_max_c"] == 34.0
    assert res["condition"] == "Clear sky"
