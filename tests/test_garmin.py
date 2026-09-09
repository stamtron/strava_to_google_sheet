"""
Unit tests for Garmin Health Biometrics & Nap Tracking.
"""

from datetime import date
from unittest.mock import MagicMock
import pytest

from src.integrations.garmin import get_weekly_health_summary


def test_get_weekly_health_summary_includes_naps_in_sleep():
    """Verify napTimeSeconds is added to sleepTimeSeconds in total_sleep_h and avg_sleep_h."""
    mock_client = MagicMock()

    # Suppose 2 days in week:
    # Day 1: 6h sleep (21600s) + 1.5h nap (5400s) = 7.5h (27000s)
    # Day 2: 7h sleep (25200s) + 0h nap (0s) = 7.0h (25200s)
    def fake_sleep_data(date_str):
        if date_str == "2026-09-01":
            return {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 21600,
                    "napTimeSeconds": 5400,
                }
            }
        elif date_str == "2026-09-02":
            return {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 25200,
                    "napTimeSeconds": 0,
                }
            }
        return None

    mock_client.get_sleep_data.side_effect = fake_sleep_data
    mock_client.get_user_summary.return_value = {"restingHeartRate": 48}
    mock_client.get_rhr_day.return_value = None
    mock_client.get_hrv_data.return_value = {"hrvSummary": {"lastNightAvg": 72}}
    mock_client.get_stress_data.return_value = {"avgStressLevel": 25}
    mock_client.get_body_battery.return_value = [{"charged": 80, "drained": 70}]
    mock_client.get_training_readiness.return_value = {"score": 85}

    summary = get_weekly_health_summary(date(2026, 9, 1), date(2026, 9, 2), client=mock_client)

    assert summary is not None
    # Total sleep: (27000 + 25200) / 3600 = 52200 / 3600 = 14.5 hours
    assert pytest.approx(summary["total_sleep_h"], 0.01) == 14.5
    # Average sleep across 2 days: 14.5 / 2 = 7.25 hours
    assert pytest.approx(summary["avg_sleep_h"], 0.01) == 7.25
    # Total nap: 5400 / 3600 = 1.5 hours
    assert pytest.approx(summary["total_nap_h"], 0.01) == 1.5
    assert summary["avg_rhr"] == 48
    assert summary["avg_hrv"] == 72


def test_get_weekly_health_summary_handles_nap_only_day():
    """A day with 0 nighttime sleep but a recorded nap still registers sleep duration."""
    mock_client = MagicMock()

    mock_client.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 0,
            "napTimeSeconds": 3600,  # 1h nap
        }
    }
    mock_client.get_user_summary.return_value = None
    mock_client.get_rhr_day.return_value = None
    mock_client.get_hrv_data.return_value = None
    mock_client.get_stress_data.return_value = None
    mock_client.get_body_battery.return_value = None
    mock_client.get_training_readiness.return_value = None

    summary = get_weekly_health_summary(date(2026, 9, 1), date(2026, 9, 1), client=mock_client)

    assert summary is not None
    assert pytest.approx(summary["total_sleep_h"], 0.01) == 1.0
    assert pytest.approx(summary["avg_sleep_h"], 0.01) == 1.0
    assert pytest.approx(summary["total_nap_h"], 0.01) == 1.0


def test_get_weekly_health_summary_handles_no_naps():
    """When napTimeSeconds is missing or 0, total_nap_h is None and sleep matches sleepTimeSeconds."""
    mock_client = MagicMock()

    mock_client.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 28800,  # 8h
        }
    }
    mock_client.get_user_summary.return_value = None
    mock_client.get_rhr_day.return_value = None
    mock_client.get_hrv_data.return_value = None
    mock_client.get_stress_data.return_value = None
    mock_client.get_body_battery.return_value = None
    mock_client.get_training_readiness.return_value = None

    summary = get_weekly_health_summary(date(2026, 9, 1), date(2026, 9, 1), client=mock_client)

    assert summary is not None
    assert pytest.approx(summary["total_sleep_h"], 0.01) == 8.0
    assert summary["total_nap_h"] is None


def test_get_daily_recovery_metrics_optimal():
    from src.integrations.garmin import get_daily_recovery_metrics

    mock_client = MagicMock()
    mock_client.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 28800,  # 8 hours
            "napTimeSeconds": 0,
            "sleepScores": {"overall": {"value": 88}},
        }
    }
    mock_client.get_user_summary.return_value = {"restingHeartRate": 48}
    mock_client.get_rhr_day.return_value = None
    mock_client.get_hrv_data.return_value = {"hrvSummary": {"lastNightAvg": 75, "status": "BALANCED"}}
    mock_client.get_stress_data.return_value = {"avgStressLevel": 20}
    mock_client.get_body_battery.return_value = [{"charged": 85, "drained": 60, "bodyBatteryValuesArray": [[0, 85]]}]

    rec = get_daily_recovery_metrics(date(2026, 9, 8), client=mock_client)
    assert rec["available"] is True
    assert rec["recovery_status"] == "optimal"
    assert rec["sleep_hours"] == 8.0
    assert rec["resting_hr"] == 48
    assert rec["hrv_last_night"] == 75
    assert rec["recovery_score"] >= 80


def test_get_daily_recovery_metrics_compromised():
    from src.integrations.garmin import get_daily_recovery_metrics

    mock_client = MagicMock()
    mock_client.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 18000,  # 5 hours
            "napTimeSeconds": 0,
            "sleepScores": {"overall": {"value": 45}},
        }
    }
    mock_client.get_user_summary.return_value = {"restingHeartRate": 64}
    mock_client.get_rhr_day.return_value = None
    mock_client.get_hrv_data.return_value = {"hrvSummary": {"lastNightAvg": 38, "status": "LOW"}}
    mock_client.get_stress_data.return_value = {"avgStressLevel": 48}
    mock_client.get_body_battery.return_value = [{"charged": 25, "drained": 20, "bodyBatteryValuesArray": [[0, 25]]}]

    rec = get_daily_recovery_metrics(date(2026, 9, 8), client=mock_client)
    assert rec["available"] is True
    assert rec["recovery_status"] in ("compromised", "poor")
    assert "critically_low_sleep" in rec["flags"]
    assert "depressed_hrv" in rec["flags"]
    assert "elevated_rhr" in rec["flags"]


def test_assess_workout_readiness_modulates_intervals_on_poor_recovery():
    from src.integrations.garmin import assess_workout_readiness

    fatigued_rec = {
        "target_date": "2026-09-08",
        "available": True,
        "recovery_status": "poor",
        "recovery_score": 35,
        "flags": ["critically_low_sleep", "depressed_hrv"],
    }
    workout_text = "Τρέξιμο 6x1000m @ 4:05 με 2' διάλειμμα"
    readiness = assess_workout_readiness(fatigued_rec, workout_text=workout_text)

    assert readiness["needs_modulation"] is True
    assert readiness["workout_detected_intensity"] == "high"
    assert "ακύρωση" in readiness["modulation_advice"] or "μείωση" in readiness["modulation_advice"]
    assert readiness["substitute_option"] is not None


def test_assess_workout_readiness_clears_rest_and_optimal():
    from src.integrations.garmin import assess_workout_readiness

    optimal_rec = {
        "target_date": "2026-09-08",
        "available": True,
        "recovery_status": "optimal",
        "recovery_score": 90,
        "flags": [],
    }
    readiness = assess_workout_readiness(optimal_rec, workout_text="Τρέξιμο 5 χλμ χαλαρό Zone 2")
    assert readiness["needs_modulation"] is False
    assert "Βέλτιστη Ετοιμότητα" in readiness["modulation_advice"]

