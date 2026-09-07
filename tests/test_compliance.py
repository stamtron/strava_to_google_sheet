"""
Unit tests for Plan vs. Actual Workout Compliance Engine.
"""

from src.analytics.compliance import evaluate_daily_compliance, parse_planned_workout


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
