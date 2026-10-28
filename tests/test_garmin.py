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
    assert readiness["advice_key"] == "poor_high_intensity"
    assert readiness["substitute_option"]["kind"] == "recovery_ride"
    assert "modulation_advice" not in readiness


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
    assert readiness["advice_key"] == "optimal"


def test_compute_biometric_baselines_and_evaluate():
    from src.integrations.garmin import compute_biometric_baselines, evaluate_biometric_status

    # Insufficient history (< 3 weeks)
    short_history = {
        "2026-W38": {"avg_hrv": 60, "avg_rhr": 50},
        "2026-W39": {"avg_hrv": 62, "avg_rhr": 48},
    }
    base = compute_biometric_baselines(short_history, min_weeks=3)
    assert base["hrv"]["weeks_used"] == 2
    assert base["hrv"]["normal_range"] is None

    stat = evaluate_biometric_status(55, base["hrv"], "hrv")
    assert stat == "unknown"

    # 4 weeks of history: Mean HRV = 60, Mean RHR = 50
    history = {
        "2026-W36": {"avg_hrv": 60, "avg_rhr": 50},
        "2026-W37": {"avg_hrv": 58, "avg_rhr": 52},
        "2026-W38": {"avg_hrv": 62, "avg_rhr": 48},
        "2026-W39": {"avg_hrv": 60, "avg_rhr": 50},
    }

    baselines = compute_biometric_baselines(history, min_weeks=3)
    assert baselines["hrv"]["weeks_used"] == 4
    assert pytest.approx(baselines["hrv"]["baseline_mean"], 0.1) == 60.0
    assert pytest.approx(baselines["rhr"]["baseline_mean"], 0.1) == 50.0
    assert baselines["hrv"]["normal_range"][0] < 60.0
    assert baselines["hrv"]["normal_range"][1] > 60.0

    # Normal day (within normal_range)
    hrv_stat = evaluate_biometric_status(60, baselines["hrv"], "hrv")
    rhr_stat = evaluate_biometric_status(50, baselines["rhr"], "rhr")
    assert hrv_stat == "optimal"
    assert rhr_stat == "optimal"

    # Suppressed HRV & Elevated RHR
    low_hrv = evaluate_biometric_status(45, baselines["hrv"], "hrv")
    high_rhr = evaluate_biometric_status(60, baselines["rhr"], "rhr")
    assert low_hrv == "low"
    assert high_rhr == "elevated"





# --- Weekly cache: failures vs genuinely empty weeks ---

import json

from src.integrations import garmin
from src.integrations.garmin import assess_workout_readiness, get_weekly_health_summaries


def _empty_client():
    client = MagicMock()
    for name in (
        "get_sleep_data", "get_user_summary", "get_rhr_day", "get_hrv_data",
        "get_stress_data", "get_body_battery", "get_training_readiness",
    ):
        getattr(client, name).return_value = None
    return client


@pytest.fixture
def garmin_cache(tmp_path, monkeypatch):
    path = tmp_path / "garmin_cache.json"
    monkeypatch.setattr(garmin, "GARMIN_CACHE_FILE", str(path))
    monkeypatch.setattr(garmin, "today_local", lambda: date(2026, 10, 28))
    return path


WEEK = {"2026-09-07": (date(2026, 9, 7), date(2026, 9, 13))}


def test_failed_week_is_not_cached_as_empty(garmin_cache, monkeypatch):
    client = _empty_client()
    client.get_sleep_data.side_effect = ConnectionError("garmin down")
    monkeypatch.setattr(garmin, "get_garmin_client", lambda: client)

    assert get_weekly_health_summaries(WEEK) == {}
    assert not garmin_cache.exists() or "2026-09-07" not in json.loads(garmin_cache.read_text())


def test_empty_week_is_cached_only_for_the_short_ttl(garmin_cache, monkeypatch):
    client = _empty_client()
    monkeypatch.setattr(garmin, "get_garmin_client", lambda: client)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(garmin.time, "time", lambda: clock["now"])

    get_weekly_health_summaries(WEEK)
    assert json.loads(garmin_cache.read_text())["2026-09-07"]["summary"] is None
    calls = client.get_sleep_data.call_count

    get_weekly_health_summaries(WEEK)  # still fresh: no refetch
    assert client.get_sleep_data.call_count == calls

    clock["now"] += garmin.GARMIN_EMPTY_CACHE_TTL + 1
    get_weekly_health_summaries(WEEK)  # finished week, but empty: re-checked
    assert client.get_sleep_data.call_count > calls


def test_partial_week_is_flagged_and_rechecked(garmin_cache, monkeypatch):
    client = _empty_client()
    client.get_hrv_data.return_value = {"hrvSummary": {"lastNightAvg": 70}}
    client.get_stress_data.side_effect = TimeoutError("slow")
    monkeypatch.setattr(garmin, "get_garmin_client", lambda: client)

    res = get_weekly_health_summaries(WEEK)
    assert res["2026-09-07"]["avg_hrv"] == 70
    entry = json.loads(garmin_cache.read_text())["2026-09-07"]
    assert entry["partial"] is True
    assert garmin._cache_entry_is_fresh(entry, date(2026, 9, 13), date(2026, 10, 28))
    entry["fetched_at"] -= garmin.GARMIN_EMPTY_CACHE_TTL + 1
    assert not garmin._cache_entry_is_fresh(entry, date(2026, 9, 13), date(2026, 10, 28))


def test_complete_finished_week_stays_cached(garmin_cache):
    entry = {"summary": {"avg_hrv": 70}, "fetched_at": 0}
    assert garmin._cache_entry_is_fresh(entry, date(2026, 9, 13), date(2026, 10, 28))


# --- Login cooldown & auth invalidation ---

@pytest.fixture
def garmin_login(monkeypatch):
    monkeypatch.setattr(garmin, "GARMIN_EMAIL", "a@b.c")
    monkeypatch.setattr(garmin, "GARMIN_PASSWORD", "pw")
    monkeypatch.setattr(garmin, "_client", None)
    monkeypatch.setattr(garmin, "_client_failed_at", None)
    monkeypatch.setattr(garmin.os, "makedirs", lambda *a, **k: None)
    clock = {"now": 5_000.0}
    monkeypatch.setattr(garmin.time, "time", lambda: clock["now"])
    attempts = []

    class FakeGarmin:
        fail = True

        def __init__(self, *args):
            attempts.append(1)

        def login(self, tokenstore=None):
            if FakeGarmin.fail:
                raise ConnectionError("no network")

    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    return FakeGarmin, attempts, clock


def test_failed_login_is_retried_after_cooldown(garmin_login):
    fake, attempts, clock = garmin_login

    assert garmin.get_garmin_client() is None
    assert garmin.get_garmin_client() is None  # within cooldown: no new attempt
    assert len(attempts) == 1

    clock["now"] += garmin.GARMIN_LOGIN_RETRY_COOLDOWN_SEC + 1
    fake.fail = False
    assert isinstance(garmin.get_garmin_client(), fake)
    assert len(attempts) == 2


def test_auth_error_invalidates_cached_client(garmin_login):
    from garminconnect import GarminConnectAuthenticationError

    fake, attempts, _ = garmin_login
    fake.fail = False
    client = garmin.get_garmin_client()
    assert garmin.get_garmin_client() is client

    garmin._record_call_error([], "sleep", "2026-10-28", GarminConnectAuthenticationError("expired"))
    assert garmin._client is None
    assert garmin.get_garmin_client() is not client
    assert len(attempts) == 2


# --- Readiness: rest-day detection ---

def test_interval_session_with_rest_between_reps_is_not_a_rest_day():
    poor = {"recovery_status": "poor", "recovery_score": 30, "flags": ["low_sleep"]}
    res = assess_workout_readiness(poor, "Τρέξιμο: 8x200m με 90'' rest")
    assert res["workout_detected_intensity"] == "high"
    assert res["needs_modulation"] is True


def test_rest_keyword_inside_other_words_is_not_a_rest_day():
    ok = {"recovery_status": "adequate", "recovery_score": 70}
    res = assess_workout_readiness(ok, "Offroad trail run, χαλαρό")
    assert res["workout_detected_intensity"] == "moderate"


def test_accented_greek_rest_day_is_detected():
    ok = {"recovery_status": "adequate", "recovery_score": 70}
    assert assess_workout_readiness(ok, "Ξεκούραση")["workout_detected_intensity"] == "rest"
    assert assess_workout_readiness(ok, "Day off")["workout_detected_intensity"] == "rest"
    assert assess_workout_readiness(ok, "")["workout_detected_intensity"] == "rest"


def test_accented_greek_interval_keyword_is_high_intensity():
    ok = {"recovery_status": "adequate", "recovery_score": 70}
    res = assess_workout_readiness(ok, "Διαλειμματική προπόνηση στο στίβο")
    assert res["workout_detected_intensity"] == "high"
