"""Race/triathlon prediction tests, including API unit-language consistency."""

from src.analytics.ai_coach import (
    calculate_readiness_score,
    format_pace_min_km,
    format_race_time,
    planned_run_base_pace_sec,
    predict_race_performances,
    predict_triathlon_performances,
)
from src.config import (
    DEFAULT_RUN_PACE_SEC_PER_KM,
    READINESS_BASE_SCORE,
    READINESS_HRV_HIGH_BONUS,
    READINESS_HRV_LOW_PENALTY,
    READINESS_SCORE_MAX,
    READINESS_SCORE_MIN,
    READINESS_SLEEP_GOOD_BONUS,
    READINESS_SLEEP_LOW_PENALTY,
    TRI_LEG_FACTORS,
)

GREEK_UNITS = ("χλμ", "/100μ", "χλμ/ω")


def _run(dist_km: float, time_sec: int) -> dict:
    return {
        "sport_type": "Run",
        "distance": dist_km * 1000.0,
        "moving_time": time_sec,
        "average_speed": (dist_km * 1000.0) / time_sec,
    }


def test_pace_formatting_uses_english_units():
    assert format_pace_min_km(310.0) == "5:10 /km"
    assert format_pace_min_km(0) == "-:--"
    assert format_pace_min_km(float("inf")) == "-:--"


def test_race_time_formatting():
    assert format_race_time(1554) == "25m 54s"
    assert format_race_time(12000) == "3h 20m 00s"


def test_predictions_report_english_units_only():
    """One payload must not mix Greek and English units."""
    pred = predict_race_performances([_run(10.0, 3100)])
    blob = repr(pred)
    for unit in GREEK_UNITS:
        assert unit not in blob, f"Greek unit {unit!r} leaked into API metrics"
    assert "/km" in pred["base_pace_used"]


def test_triathlon_baselines_report_english_units_only():
    activities = [
        _run(10.0, 3100),
        {"sport_type": "Ride", "distance": 40000.0, "moving_time": 5400, "average_speed": 40000 / 5400},
        {"sport_type": "Swim", "distance": 3000.0, "moving_time": 2700, "average_speed": 3000 / 2700},
    ]
    tri = predict_triathlon_performances(activities)
    blob = repr(tri)
    for unit in GREEK_UNITS:
        assert unit not in blob, f"Greek unit {unit!r} leaked into API metrics"

    baselines = tri["baselines"]
    assert "/100m" in baselines["swim_100m"]
    assert "km/h" in baselines["bike_speed"]
    assert "/km" in baselines["run_pace"]


def test_longer_races_are_slower_per_km_than_the_5k():
    """Riegel's model must fatigue, not extrapolate 5K pace flat."""
    pred = predict_race_performances([_run(5.0, 1500)])
    by_name = {p["name"]: p for p in pred["predictions"]}
    five = by_name["5K"]
    times = [p["predicted_time_seconds"] for p in pred["predictions"]]

    assert times == sorted(times)  # longer race, longer time
    marathon = pred["predictions"][-1]
    assert marathon["predicted_time_seconds"] / 42.195 > five["predicted_time_seconds"] / 5.0


def test_custom_5k_pace_overrides_activity_history():
    pred = predict_race_performances([_run(5.0, 1500)], custom_5k_pace_sec=240.0)
    assert pred["base_pace_used"] == "4:00 /km"


def test_predictions_fall_back_without_run_history():
    pred = predict_race_performances([])
    assert pred["base_pace_used"] == "5:00 /km"  # documented default
    assert len(pred["predictions"]) == 4


def test_swim_baseline_accounts_for_the_distance_correction():
    """
    Swim pace must be derived from corrected distance, so it agrees with the
    sheet and dashboard rather than reading twice as fast.
    """
    tri = predict_triathlon_performances(
        [{"sport_type": "Swim", "distance": 3000.0, "moving_time": 2700, "average_speed": 3000 / 2700}]
    )
    mins, _, rest = tri["baselines"]["swim_100m"].partition(":")
    secs = int(rest.split()[0])
    pace_sec = int(mins) * 60 + secs
    # 1500m corrected in 2700s -> 3:00 /100m, clamped to the realistic ceiling.
    assert pace_sec >= 105.0


def test_triathlon_distances_are_ordered_by_total_time():
    activities = [
        _run(10.0, 3100),
        {"sport_type": "Ride", "distance": 40000.0, "moving_time": 5400, "average_speed": 40000 / 5400},
        {"sport_type": "Swim", "distance": 3000.0, "moving_time": 2700, "average_speed": 3000 / 2700},
    ]
    tri = predict_triathlon_performances(activities)
    totals = [p["total_time_seconds"] for p in tri["predictions"]]
    assert totals == sorted(totals)
    assert all(p["splits"] for p in tri["predictions"])


# --- readiness heuristic ------------------------------------------------------


def test_readiness_is_the_base_score_without_data():
    assert calculate_readiness_score(None, None) == READINESS_BASE_SCORE


def test_readiness_rewards_good_sleep_and_high_hrv():
    expected = READINESS_BASE_SCORE + READINESS_SLEEP_GOOD_BONUS + READINESS_HRV_HIGH_BONUS
    assert calculate_readiness_score(8.0, 75.0) == min(READINESS_SCORE_MAX, expected)


def test_readiness_penalises_short_sleep_and_low_hrv():
    expected = READINESS_BASE_SCORE - READINESS_SLEEP_LOW_PENALTY - READINESS_HRV_LOW_PENALTY
    assert calculate_readiness_score(5.0, 40.0) == max(READINESS_SCORE_MIN, expected)


def test_readiness_middle_band_is_neutral():
    assert calculate_readiness_score(7.0, 60.0) == READINESS_BASE_SCORE


def test_readiness_is_clamped(monkeypatch):
    from src.analytics import ai_coach

    monkeypatch.setattr(ai_coach, "READINESS_SLEEP_GOOD_BONUS", 500)
    assert calculate_readiness_score(9.0, None) == READINESS_SCORE_MAX
    monkeypatch.setattr(ai_coach, "READINESS_SLEEP_LOW_PENALTY", 500)
    assert calculate_readiness_score(3.0, None) == READINESS_SCORE_MIN


# --- planned run pace parsing ------------------------------------------------


def test_planned_pace_reads_the_at_target():
    assert planned_run_base_pace_sec("Τρέξιμο 8 χλμ @ 5:00") == 300.0
    assert planned_run_base_pace_sec("Easy run 10k @4,45") == 285.0


def test_planned_pace_falls_back_to_default_for_runs_without_a_target():
    assert planned_run_base_pace_sec("Δρομικές ασκήσεις 30'") == DEFAULT_RUN_PACE_SEC_PER_KM


def test_planned_pace_is_none_for_non_runs():
    assert planned_run_base_pace_sec("Ποδήλατο 60' Z2") is None
    assert planned_run_base_pace_sec("") is None
    assert planned_run_base_pace_sec(None) is None


# --- triathlon factors come from config --------------------------------------


def test_triathlon_transitions_follow_config_for_pb_and_training_modes():
    for use_race_pb, idx in ((True, 0), (False, 1)):
        preds = predict_triathlon_performances([], use_race_pb=use_race_pb)["predictions"]
        assert len(preds) == len(TRI_LEG_FACTORS)
        for row, factors in zip(preds, TRI_LEG_FACTORS.values(), strict=True):
            assert row["splits"]["t1"] == format_race_time(factors["t1_sec"][idx])
            assert row["splits"]["t2"] == format_race_time(factors["t2_sec"][idx])
