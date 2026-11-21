"""Unit conversion tests."""

from src.formatting import (
    distance_and_speed,
    format_duration,
    format_duration_el,
    format_duration_short_el,
    format_mmss,
    format_pace,
    parse_pace_to_sec,
)


def test_format_duration_with_and_without_hours():
    assert format_duration(5070) == "1h 24m 30s"
    assert format_duration(1470) == "24m 30s"
    assert format_duration(0) == "0s"
    assert format_duration(None) == "0s"


def test_format_duration_el_omits_zero_components():
    assert format_duration_el(5070) == "1ω 24λ 30δ"
    assert format_duration_el(3600) == "1ω"
    assert format_duration_el(90) == "1λ 30δ"
    assert format_duration_el(0) == "0δ"


def test_format_duration_short_el_drops_seconds():
    assert format_duration_short_el(5070) == "1ω 24λ"
    assert format_duration_short_el(1470) == "24λ"
    assert format_duration_short_el(0) == "0λ"


def test_format_pace_is_sport_specific():
    # 1000m in 300s -> 5:00/km
    assert format_pace(1000 / 300, "Run") == "5:00 /km"
    assert format_pace(1000 / 300, "Run", greek=True) == "5:00 /χλμ"
    # 100m in 100s -> 1:40/100m
    assert format_pace(1.0, "Swim") == "1:40 /100m"
    # Anything else reads as km/h
    assert format_pace(10.0, "Ride") == "36.0 km/h"


def test_format_pace_handles_missing_speed():
    assert format_pace(0, "Run") == "—"
    assert format_pace(None, "Run", greek=True) == "N/A"


def test_distances_are_taken_as_strava_reports_them():
    swim = {"sport_type": "Swim", "distance": 2000.0, "average_speed": 1.0, "moving_time": 1800}
    tacx = {"sport_type": "VirtualRide", "distance": 30000.0, "average_speed": 7.5, "moving_time": 4000, "trainer": True}
    assert distance_and_speed(swim) == (2000.0, 1.0)
    assert distance_and_speed(tacx) == (30000.0, 7.5)


def test_distance_and_speed_tolerate_missing_fields():
    assert distance_and_speed({"sport_type": "Run", "distance": None}) == (0.0, 0.0)


def test_format_mmss_rounds_and_clamps():
    assert format_mmss(315) == "5:15"
    assert format_mmss(59.6) == "1:00"
    assert format_mmss(-5) == "0:00"


def test_parse_pace_to_sec_accepts_sheet_notations():
    assert parse_pace_to_sec("5:15/km") == 315.0
    assert parse_pace_to_sec("5,15''") == 315.0
    assert parse_pace_to_sec("@ 4.50") == 290.0
    assert parse_pace_to_sec("easy") is None
    assert parse_pace_to_sec("") is None
