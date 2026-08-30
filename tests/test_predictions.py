"""Race/triathlon prediction tests, including API unit-language consistency."""

from src.analytics.ai_coach import (
    format_pace_min_km,
    format_race_time,
    predict_race_performances,
    predict_triathlon_performances,
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
