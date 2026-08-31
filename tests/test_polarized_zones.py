"""
Unit tests for Heart Rate Zones and 80/20 Polarized Training metrics.
"""

import pytest
from src.analytics.metrics import (
    calculate_hr_zones,
    calculate_polarized_distribution,
    estimate_activity_zone_times,
)


def test_calculate_hr_zones():
    zones = calculate_hr_zones(hr_max=185, hr_rest=50)
    # HRR = 135
    # Z1: 50 + 67.5 = 118 -> 131
    # Z2: 132 -> 147
    # Z3: 148 -> 161
    # Z4: 162 -> 172
    # Z5: 173 -> 185
    assert zones["z1"][0] >= 50
    assert zones["z1"][1] < zones["z2"][0]
    assert zones["z2"][1] < zones["z3"][0]
    assert zones["z3"][1] < zones["z4"][0]
    assert zones["z4"][1] < zones["z5"][0]
    assert zones["z5"][1] == 185


def test_estimate_activity_zone_times():
    # Easy Z2 run (moving time 3600s, avg HR 135)
    act_easy = {
        "sport_type": "Run",
        "moving_time": 3600,
        "average_heartrate": 135,
    }
    z_times = estimate_activity_zone_times(act_easy, hr_max=185, hr_rest=50)
    assert sum(z_times.values()) == pytest.approx(3600, abs=1)
    assert z_times["z2"] > z_times["z4"]  # Predominantly Z2


def test_calculate_polarized_distribution_80_20():
    # 4 easy Z1/Z2 workouts and 1 high intensity interval session
    acts = [
        {"sport_type": "Run", "moving_time": 3600, "average_heartrate": 130}, # Z2
        {"sport_type": "Ride", "moving_time": 7200, "average_heartrate": 125}, # Z1/Z2
        {"sport_type": "Swim", "moving_time": 2400, "average_heartrate": 132}, # Z2
        {"sport_type": "Run", "moving_time": 3000, "average_heartrate": 175}, # Z4/Z5 high
    ]
    res = calculate_polarized_distribution(acts, hr_max=185, hr_rest=50)
    assert res["total_time_sec"] == 3600 + 7200 + 2400 + 3000
    assert res["low_pct"] >= 70.0
    assert res["classification"] in ("polarized", "pyramidal", "balanced")
