"""
Unit tests for Weather-Adjusted Pacing Calculator.
"""

from src.analytics.weather_pace import (
    calculate_weather_pace_adjustment,
    format_sec_to_pace_string,
    parse_pace_string_to_sec,
)


def test_parse_pace_string():
    assert parse_pace_string_to_sec("5:15") == 315.0
    assert parse_pace_string_to_sec("5,15''") == 315.0
    assert parse_pace_string_to_sec("4:30/km") == 270.0
    assert parse_pace_string_to_sec("6.00") == 360.0
    assert parse_pace_string_to_sec("invalid") is None
    assert parse_pace_string_to_sec("") is None


def test_format_sec_to_pace():
    assert format_sec_to_pace_string(315.0) == "5:15/χλμ"
    assert format_sec_to_pace_string(270.0) == "4:30/χλμ"
    assert format_sec_to_pace_string(365.4) == "6:05/χλμ"


def test_cool_weather_no_adjustment():
    res = calculate_weather_pace_adjustment(315.0, temp_c=14.0, wind_kmh=10.0)
    assert res["needs_adjustment"] is False
    assert res["adjustment_sec"] == 0
    assert res["heat_impact"] == "None"


def test_hot_weather_pace_adjustment():
    # 33°C heat -> 8.5% thermal penalty -> ~26.8 sec slower
    res = calculate_weather_pace_adjustment(315.0, temp_c=33.0, wind_kmh=12.0)
    assert res["needs_adjustment"] is True
    assert res["adjusted_pace_sec"] > 315.0
    assert "High Heat" in res["heat_impact"]
    assert "Ζώνη 2" in res["advice"]


def test_heat_and_wind_combined():
    res = calculate_weather_pace_adjustment(300.0, temp_c=32.0, wind_kmh=28.0)
    # 8.5% heat + 3.0% wind = 11.5% adjustment = +34.5 sec
    assert res["needs_adjustment"] is True
    assert res["adjustment_sec"] >= 30.0
    assert res["wind_impact"] == "Moderate Wind (26–35 km/h)"
