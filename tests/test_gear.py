"""
Unit tests for Gear and Shoe Mileage Tracker.
"""

from src.analytics.gear import extract_gear_summary
from src.config import GEAR_BIKE_ALERT_KM, GEAR_WARNING_FRACTION


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


def test_extract_gear_summary_warning_band():
    acts = [
        {
            "id": 1,
            "sport_type": "Run",
            "distance": 560000.0,  # 560 km / 650 km = 86.1% -> warning
            "gear_id": "g_shoes_warn",
            "start_date_local": "2026-09-01T08:00:00Z",
        }
    ]
    summary = extract_gear_summary(acts, shoe_alert_km=650)
    assert len(summary) == 1
    assert summary[0]["status"] == "warning"
    assert summary[0]["alert"] is False
    assert summary[0]["remaining_km"] == 90.0


def test_api_gear_and_performance_endpoints(monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from src.api.server import app
    fake_cache = {
        "timestamp": time.time(),
        "count": 200,
        "activities": [
            {
                "id": 101,
                "sport_type": "Run",
                "distance": 10000.0,
                "gear_id": "g_shoe_api",
                "start_date_local": "2026-10-01T08:00:00Z",
                "moving_time": 3000,
                "suffer_score": 50,
            }
        ],
        "details": {},
    }
    monkeypatch.setattr("src.api.server._load_cache", lambda: fake_cache)

    client = TestClient(app)

    # 1. Test GET /api/gear
    resp_gear = client.get("/api/gear")
    assert resp_gear.status_code == 200
    gear_data = resp_gear.json()
    assert "gear" in gear_data
    assert len(gear_data["gear"]) == 1
    assert gear_data["gear"][0]["gear_id"] == "g_shoe_api"

    # 2. Test GET /api/performance
    resp_perf = client.get("/api/performance")
    assert resp_perf.status_code == 200
    perf_data = resp_perf.json()
    assert "current" in perf_data
    assert "series" in perf_data
    assert "ctl" in perf_data["current"]
    assert "tsb" in perf_data["current"]



def test_gear_type_follows_the_strava_id_prefix():
    acts = [
        # Shoes worn on a walk are still shoes.
        {"id": 1, "sport_type": "Walk", "distance": 5000.0, "gear_id": "g123",
         "start_date_local": "2026-09-01T08:00:00Z"},
        # A bike id on an unknown sport is still a bike.
        {"id": 2, "sport_type": "Workout", "distance": 20000.0, "gear_id": "b456",
         "start_date_local": "2026-09-02T08:00:00Z"},
    ]
    by_id = {g["gear_id"]: g for g in extract_gear_summary(acts, shoe_alert_km=650)}
    assert by_id["g123"]["gear_type"] == "Shoes"
    assert by_id["g123"]["threshold_km"] == 650.0
    assert by_id["b456"]["gear_type"] == "Bike"
    assert by_id["b456"]["threshold_km"] == GEAR_BIKE_ALERT_KM


def test_unprefixed_gear_falls_back_to_the_activity_sport():
    acts = [{"id": 1, "sport_type": "Ride", "distance": 10000.0, "gear_id": "x9",
             "start_date_local": "2026-09-01T08:00:00Z"}]
    assert extract_gear_summary(acts)[0]["gear_type"] == "Bike"


def test_bike_warning_band_uses_the_configured_fraction():
    km = GEAR_BIKE_ALERT_KM * GEAR_WARNING_FRACTION + 1
    acts = [{"id": 1, "sport_type": "Ride", "distance": km * 1000.0, "gear_id": "b1",
             "start_date_local": "2026-09-01T08:00:00Z"}]
    assert extract_gear_summary(acts)[0]["status"] == "warning"
