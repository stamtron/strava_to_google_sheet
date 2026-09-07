"""
Unit tests for Gear and Shoe Mileage Tracker.
"""

from src.analytics.gear import extract_gear_summary


def test_extract_gear_summary_with_retirement_alert():
    acts = [
        {
            "id": 1,
            "sport_type": "Run",
            "distance": 10000.0,
            "gear_id": "g_shoes_1",
            "start_date_local": "2026-08-01T08:00:00Z",
        },
        {
            "id": 2,
            "sport_type": "Run",
            "distance": 650000.0,  # 650 km
            "gear_id": "g_shoes_1",
            "start_date_local": "2026-08-20T08:00:00Z",
        },
        {
            "id": 3,
            "sport_type": "Ride",
            "distance": 50000.0,
            "gear_id": "b_bike_1",
            "start_date_local": "2026-08-15T08:00:00Z",
        },
    ]
    details = {
        "1": {"gear": {"name": "Saucony Triumph 21"}},
        "3": {"gear": {"name": "Canyon Aeroad"}},
    }

    summary = extract_gear_summary(acts, details=details, shoe_alert_km=650)
    assert len(summary) == 2

    shoes = next(g for g in summary if g["gear_id"] == "g_shoes_1")
    assert shoes["name"] == "Saucony Triumph 21"
    assert shoes["total_km"] == 660.0
    assert shoes["alert"] is True
    assert shoes["status"] == "retire"

    bike = next(g for g in summary if g["gear_id"] == "b_bike_1")
    assert bike["name"] == "Canyon Aeroad"
    assert bike["total_km"] == 50.0
    assert bike["alert"] is False
    assert bike["status"] == "optimal"
