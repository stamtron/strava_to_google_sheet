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
    from fastapi.testclient import TestClient
    from src.api.server import app

    import time
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

