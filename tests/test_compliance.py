"""
Unit tests for Plan vs. Actual Workout Compliance Engine.
"""

import pytest

from src.analytics.compliance import evaluate_daily_compliance, parse_planned_workout
from src.formatting import distance_and_speed


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
    distance_m, _ = distance_and_speed(act)
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
    assert m["status"] == "spot_on"
    assert res["compliance_score"] == 90  # 89.6% of the plan, not a flat 100
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


# Session classification


def test_classifier_flags_intervals_and_long_runs_as_key():
    from src.analytics.session_type import classify_planned_session

    assert classify_planned_session("Τρέξιμο: 6x800m @ 4:10 με 90'' rest")["is_key"] is True
    long_run = classify_planned_session("Τρέξιμο 18 χλμ χαλαρά")
    assert long_run["is_long_run"] is True and long_run["intensity"] == "moderate"
    assert classify_planned_session("Τρέξιμο 100' χαλαρό")["is_long_run"] is True
    assert classify_planned_session("Τρέξιμο 8 χλμ χαλαρό")["is_key"] is False
    assert classify_planned_session("Ρεπό")["intensity"] == "rest"
    # A long ride is not the long run.
    assert classify_planned_session("Ποδηλασία 40 χλμ")["is_long_run"] is False


# Weekly compliance


def _week(plan: dict[str, str], acts: list[dict], today: str):
    from datetime import date

    from src.analytics.compliance import evaluate_weekly_compliance, week_day_inputs

    plan_texts = {date.fromisoformat(k): v for k, v in plan.items()}
    return evaluate_weekly_compliance(
        week_day_inputs(date(2026, 11, 16), plan_texts, acts), date.fromisoformat(today)
    )


def test_weekly_compliance_totals_km_and_key_sessions():
    from tests.conftest import make_activity

    plan = {
        "2026-11-16": "Τρέξιμο 10 χλμ χαλαρό",
        "2026-11-18": "Τρέξιμο: 6x800m @ 4:10",
        "2026-11-20": "Ρεπό",
        "2026-11-22": "Τρέξιμο 18 χλμ χαλαρά",
    }
    acts = [
        make_activity(1, "2026-11-16", distance=9000.0),
        make_activity(2, "2026-11-18", distance=8000.0),
        make_activity(3, "2026-11-22", distance=18000.0),
    ]
    week = _week(plan, acts, today="2026-11-22")
    assert week["planned_run_km"] == 28.0  # 10 + 18; the rep set has no honest total
    assert week["actual_run_km"] == 35.0
    assert week["key_sessions_planned"] == 2
    assert week["key_sessions_done"] == 2
    assert week["sessions_planned"] == 3 and week["sessions_done"] == 3
    # Unplanned empty days don't count; the honoured rest day does.
    assert week["scored_days"] == 4
    assert week["in_progress"] is True
    states = [d["state"] for d in week["days"]]
    assert states == ["scored"] * 7


def test_weekly_compliance_does_not_mark_down_today_or_future_days():
    from tests.conftest import make_activity

    plan = {
        "2026-11-16": "Τρέξιμο 10 χλμ",
        "2026-11-18": "Τρέξιμο 10 χλμ",  # today, not done yet
        "2026-11-20": "Τρέξιμο 10 χλμ",  # future
    }
    week = _week(plan, [make_activity(1, "2026-11-16", distance=10000.0)], today="2026-11-18")
    days = {d["date"]: d for d in week["days"]}
    assert days["2026-11-18"]["state"] == "pending"
    assert days["2026-11-20"]["state"] == "future"
    assert days["2026-11-20"]["compliance_score"] is None
    assert week["weekly_score"] == 100
    assert week["planned_run_km_to_date"] == 10.0
    assert week["planned_run_km"] == 30.0


def test_weekly_compliance_counts_a_missed_key_session():
    plan = {"2026-11-17": "Τρέξιμο: 5x1000m @ 4:00"}
    week = _week(plan, [], today="2026-11-22")
    assert week["key_sessions_planned"] == 1
    assert week["key_sessions_done"] == 0
    assert week["weekly_score"] == 0


def test_weekly_km_trusts_final_surge_session_total_over_reps():
    plan = {"2026-11-21": "Run\nWorkout: Run\n\nPlanned: 9 km\n\n3km προθέρμανση\n2x2000@42k(90'')\n5x200@5k(1')"}
    assert _week(plan, [], today="2026-11-22")["planned_run_km"] == 9.0


def test_week_without_any_plan_has_no_score():
    from tests.conftest import make_activity

    week = _week({}, [make_activity(1, "2026-11-17")], today="2026-11-22")
    assert week["has_plan"] is False
    assert week["weekly_score"] is None
    assert week["actual_run_km"] == 10.0
