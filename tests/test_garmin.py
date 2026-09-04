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
