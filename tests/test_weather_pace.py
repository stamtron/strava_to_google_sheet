"""
Unit tests for Weather-Adjusted Pacing Calculator.
"""

import pytest

from src.analytics.weather_pace import (
    calculate_weather_pace_adjustment,
    parse_pace_string_to_sec,
)


def test_parse_pace_string():
    assert parse_pace_string_to_sec("5:15") == 315.0
    assert parse_pace_string_to_sec("5,15''") == 315.0
    assert parse_pace_string_to_sec("4:30/km") == 270.0
    assert parse_pace_string_to_sec("6.00") == 360.0
    assert parse_pace_string_to_sec("invalid") is None
    assert parse_pace_string_to_sec("") is None


def test_cool_weather_no_adjustment():
    res = calculate_weather_pace_adjustment(315.0, temp_c=14.0, wind_kmh=10.0)
    assert res["needs_adjustment"] is False
    assert res["adjustment_sec"] == 0
    assert res["heat_impact"] == "none"
    assert res["advice_keys"] == []


def test_hot_weather_pace_adjustment():
    # 33°C heat -> 8.5% thermal penalty -> ~26.8 sec slower
    res = calculate_weather_pace_adjustment(315.0, temp_c=33.0, wind_kmh=12.0)
    assert res["needs_adjustment"] is True
    assert res["adjusted_pace_sec"] > 315.0
    assert res["heat_impact"] == "high"
    assert "heat_hr_zone2" in res["advice_keys"]
    assert res["adjusted_pace_min_sec"] == pytest.approx(res["adjusted_pace_sec"] - 3, abs=0.2)
    assert res["adjusted_pace_max_sec"] == pytest.approx(res["adjusted_pace_sec"] + 5, abs=0.2)


def test_heat_and_wind_combined():
    res = calculate_weather_pace_adjustment(300.0, temp_c=32.0, wind_kmh=28.0)
    # 8.5% heat + 3.0% wind = 11.5% adjustment = +34.5 sec
    assert res["needs_adjustment"] is True
    assert res["adjustment_sec"] >= 30.0
    assert res["wind_impact"] == "moderate"
    assert res["advice_keys"] == ["heat_hr_zone2", "wind_steady_effort"]
