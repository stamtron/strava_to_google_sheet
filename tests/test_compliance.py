"""
Unit tests for Plan vs. Actual Workout Compliance Engine.
"""

import pytest

from src.analytics.compliance import evaluate_daily_compliance, parse_planned_workout
from src.formatting import corrected_distance_and_speed


def test_parse_planned_workout_running_intervals():
    text = (
        "ΤΡΙ\n8/9\nΤρέξιμο :\n20' ζέσταμα / δρομικές / 4χ100μ ανοίγματα και\n"
        "8χλμ @ 5,15''\n10' αποθεραπεία."
    )
    items = parse_planned_workout(text)
    assert len(items) >= 1
    run_item = items[0]
    assert run_item["sport"] == "Run"
    assert run_item["target_dist_km"] == 8.0
    assert run_item["target_pace_sec"] == 315.0


def test_parse_planned_workout_swim_and_strength():
    text = (
        "ΔΕΥ\n7/9\nΠρωί: Κολύμβηση 1900μ ως εξής:\n"
        "400μ χαλαρά / 4χ50μ μόνο πόδια\n\n"
        "Απόγευμα: Ενδυνάμωση 45' ."
    )
    items = parse_planned_workout(text)
    assert len(items) == 2
    assert items[0]["sport"] == "Swim"
    assert items[0]["target_dist_km"] == 1.9
    assert items[1]["sport"] == "WeightTraining"
    assert items[1]["target_duration_min"] == 45.0


def test_evaluate_compliance_spot_on():
    plan = "Τρέξιμο 8χλμ @ 5,15''"
    # Athlete logged 8.15 km in 45m (2700s / 8.15km = 331s/km)
    acts = [{
        "id": 1001,
        "name": "Evening Run",
        "sport_type": "Run",
        "distance": 8150.0,
        "moving_time": 2700,
    }]
    res = evaluate_daily_compliance(plan, acts)
    assert res["compliance_score"] >= 90
    assert len(res["matches"]) == 1
    assert res["matches"][0]["status"] == "spot_on"


def test_evaluate_compliance_rest_day_honored():
    plan = "Ρεπό — Rest day."
    acts = []
    res = evaluate_daily_compliance(plan, acts)
    assert res["compliance_score"] == 100
    assert res["status"] == "rest_day_honored"


def test_evaluate_compliance_rest_day_broken():
    plan = "Ρεπό"
    acts = [{
        "id": 1002,
        "name": "Hard Ride",
        "sport_type": "Ride",
        "distance": 45000.0,
        "moving_time": 5400,
    }]
    res = evaluate_daily_compliance(plan, acts)
    assert res["status"] == "rest_day_broken"
    assert res["compliance_score"] < 50


def test_evaluate_compliance_duration_targets():
    # Prescribed 60' run, athlete logs 90' (overreach)
    plan = "Τρέξιμο 60' rpe 3-4"
    acts = [{
        "id": 1003,
        "name": "Longer Run",
        "sport_type": "Run",
        "distance": 15000.0,
        "moving_time": 5400,  # 90 minutes
    }]
    res = evaluate_daily_compliance(plan, acts)
    assert res["status"] == "evaluated"
    assert len(res["matches"]) == 1
    match = res["matches"][0]
    assert match["status"] == "overreach"
    assert match["dist_compliance_pct"] is None  # no distance in plan
    assert match["dur_compliance_pct"] == 150.0  # 90 / 60
    assert "summary" not in res



def _run(distance_m: float, moving_time: int, sport: str = "Run") -> dict:
    return {"id": 2000, "name": "Session", "sport_type": sport, "distance": distance_m, "moving_time": moving_time}


def test_compliance_bands_are_contiguous_at_the_upper_edge():
    plan = "Τρέξιμο 10χλμ"
    within = evaluate_daily_compliance(plan, [_run(11200.0, 3600)])["matches"][0]
    over = evaluate_daily_compliance(plan, [_run(11300.0, 3600)])["matches"][0]
    assert within["status"] == "spot_on"
    assert over["status"] == "overreach"


def test_compliance_bands_are_contiguous_at_the_lower_edge():
    plan = "Τρέξιμο 10χλμ"
    within = evaluate_daily_compliance(plan, [_run(8800.0, 3600)])["matches"][0]
    under = evaluate_daily_compliance(plan, [_run(8700.0, 3600)])["matches"][0]
    assert within["status"] == "spot_on"
    assert under["status"] == "underreach"


def test_duration_targets_use_the_same_bands_as_distance():
    plan = "Τρέξιμο 60' rpe 3-4"
    spot = evaluate_daily_compliance(plan, [_run(10000.0, int(60 * 60 * 1.12))])["matches"][0]
    over = evaluate_daily_compliance(plan, [_run(10000.0, int(60 * 60 * 1.13))])["matches"][0]
    assert spot["status"] == "spot_on"
    assert over["status"] == "overreach"


def test_swim_actual_pace_is_per_100m():
    act = _run(2000.0, 2400, sport="Swim")
    match = evaluate_daily_compliance("Κολύμβηση 2000μ", [act])["matches"][0]
    distance_m, _ = corrected_distance_and_speed(act)
    assert match["pace_unit"] == "per_100m"
    assert match["actual_pace_sec"] == pytest.approx(2400 / (distance_m / 100.0), abs=1)


def test_run_actual_pace_is_per_km():
    match = evaluate_daily_compliance("Τρέξιμο 10χλμ", [_run(10000.0, 3000)])["matches"][0]
    assert match["pace_unit"] == "per_km"
    assert match["actual_pace_sec"] == pytest.approx(300, abs=1)


def test_strides_do_not_turn_a_run_into_a_ride():
    items = parse_planned_workout("Run 8km easy + 6 strides")
    assert [i["sport"] for i in items] == ["Run"]


def test_greek_and_english_sport_keywords_are_detected():
    assert parse_planned_workout("Ποδήλατο 60'")[0]["sport"] == "Ride"
    assert parse_planned_workout("Bike 90'")[0]["sport"] == "Ride"
    assert parse_planned_workout("Swim 1500m")[0]["sport"] == "Swim"


def _timed_run(act_id, km, minutes, start):
    return {"id": act_id, "name": f"Run {act_id}", "sport_type": "Run",
            "distance": km * 1000.0, "moving_time": minutes * 60,
            "start_date_local": f"2026-11-21T{start}:00"}


def test_split_recordings_of_one_planned_run_are_summed():
    # One 9 km prescription logged as three Strava runs (main set, extra, cool-down).
    acts = [_timed_run(3, 1.03, 3, "13:40"), _timed_run(1, 3.02, 19, "12:00"), _timed_run(2, 4.01, 22, "12:30")]
    res = evaluate_daily_compliance("Τρέξιμο 9χλμ", acts)
    assert len(res["matches"]) == 1
    m = res["matches"][0]
    assert m["activity_count"] == 3
    assert m["actual_km"] == 8.06
    assert m["actual_min"] == 44.0
    assert m["dist_compliance_pct"] == round(8.06 / 9 * 100, 1)
    assert m["activity_name"] == "Run 1, Run 2, Run 3"  # chronological
    assert res["unmatched_actual"] == []


def test_two_planned_runs_pair_with_two_recordings_in_order():
    plan = "Πρωί: Τρέξιμο 5χλμ\nΑπόγευμα: Κολύμβηση 1000μ\nΒράδυ: Τρέξιμο 10χλμ"
    acts = [_timed_run(2, 10.0, 50, "19:00"), _timed_run(1, 5.0, 25, "07:00")]
    res = evaluate_daily_compliance(plan, acts)
    runs = [m for m in res["matches"] if m["sport"] == "Run"]
    assert [m["actual_km"] for m in runs] == [5.0, 10.0]
    assert all(m["status"] == "spot_on" for m in runs)
    swim = next(m for m in res["matches"] if m["sport"] == "Swim")
    assert swim["status"] == "missed" and swim["activity_count"] == 0


def test_other_sports_are_not_folded_into_a_planned_timed_run():
    acts = [_timed_run(1, 9.0, 45, "12:00"),
            {"id": 9, "name": "Ride", "sport_type": "Ride", "distance": 20000.0,
             "moving_time": 3600, "start_date_local": "2026-11-21T18:00:00"}]
    res = evaluate_daily_compliance("Τρέξιμο 9χλμ", acts)
    assert res["matches"][0]["activity_count"] == 1
    assert res["unmatched_actual"] == ["Ride"]
