"""Unit conversion and sport-correction tests."""

from src.config import INDOOR_BIKE_SPEED_KMH, SWIM_DISTANCE_DIVISOR
from src.formatting import (
    corrected_distance_and_speed,
    format_duration,
    format_duration_el,
    format_duration_short_el,
    format_pace,
    is_indoor_ride,
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


def test_swim_distance_and_speed_are_divided():
    act = {"sport_type": "Swim", "distance": 2000.0, "average_speed": 1.0, "moving_time": 1800}
    dist, speed = corrected_distance_and_speed(act)
    assert dist == 2000.0 / SWIM_DISTANCE_DIVISOR
    assert speed == 1.0 / SWIM_DISTANCE_DIVISOR


def test_indoor_ride_distance_is_estimated_from_duration():
    act = {"sport_type": "VirtualRide", "distance": 0.0, "average_speed": 0.0, "moving_time": 3600, "trainer": True}
    assert is_indoor_ride(act)
    dist, speed = corrected_distance_and_speed(act)
    assert dist == INDOOR_BIKE_SPEED_KMH * 1000.0
    assert round(speed, 4) == round(INDOOR_BIKE_SPEED_KMH / 3.6, 4)


def test_outdoor_ride_is_left_alone():
    act = {"sport_type": "Ride", "distance": 40000.0, "average_speed": 8.0, "moving_time": 5000}
    assert not is_indoor_ride(act)
    assert corrected_distance_and_speed(act) == (40000.0, 8.0)


def test_trainer_ride_with_real_distance_is_not_estimated():
    """A trainer that does report distance must keep its own numbers."""
    act = {"sport_type": "VirtualRide", "distance": 30000.0, "average_speed": 7.5, "moving_time": 4000, "trainer": True}
    assert not is_indoor_ride(act)
    assert corrected_distance_and_speed(act) == (30000.0, 7.5)
