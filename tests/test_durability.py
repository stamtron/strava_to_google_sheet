"""
Run-durability tests: ramp rate, spacing, long-run share, monotony/strain, the
combined assessment, and cross-training substitution. Pure and offline.

Thresholds are imported from config rather than written as literals, so retuning
a constant does not silently invalidate the suite — the tests are built around
the threshold, not around today's value of it.
"""

from datetime import date, timedelta

import pytest

from tests.conftest import make_activity

from src.analytics.durability import (
    SINGLE_LEG_EXERCISES,
    assess_run_durability,
    long_run_share,
    run_ramp_rate,
    run_spacing_profile,
    sport_strength_profile,
    suggest_cross_training,
    training_monotony_and_strain,
)
from src.analytics.metrics import calculate_acwr, process_activities_into_weeks
from src.config import (
    AQUA_JOG_LOAD_FACTOR,
    ATHLETE_PB_10K_SEC,
    BIKE_RUN_LOAD_FACTOR,
    MONOTONY_WARN_THRESHOLD,
    RUN_LONG_RUN_MAX_SHARE,
    RUN_MIN_REST_DAYS,
    RUN_RAMP_SAFE_PCT,
    STRAIN_WARN_THRESHOLD,
)

MONDAYS = ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24"]


def weeks_from(activities: list[dict]) -> dict:
    return process_activities_into_weeks(activities)


def run_week(monday: str, km_per_day: list[float], start_id: int = 1, **extra) -> list[dict]:
    """One run per listed day, starting on `monday`. A 0 entry means a rest day."""
    base = date.fromisoformat(monday)
    return [
        make_activity(
            start_id + offset,
            (base + timedelta(days=offset)).isoformat(),
            "Run",
            distance=km * 1000.0,
            **extra,
        )
        for offset, km in enumerate(km_per_day)
        if km > 0
    ]


def weekly_run_km(km_totals: list[float]) -> dict:
    """One week per entry, each week's volume delivered as a single Monday run."""
    acts = []
    for i, km in enumerate(km_totals):
        if km > 0:
            acts += run_week(MONDAYS[i], [km], start_id=100 * (i + 1))
    return weeks_from(acts)


# ── Ramp rate ────────────────────────────────────────────────────────────────


def test_the_first_week_has_no_predecessor_to_ramp_from():
    ramp = run_ramp_rate(weekly_run_km([20.0]))

    assert len(ramp) == 1
    assert ramp[0]["change_pct"] is None
    assert ramp[0]["prev_run_km"] is None
    assert ramp[0]["severity"] == "unknown"


def test_an_increase_within_the_safe_threshold_is_not_flagged():
    safe = 20.0 * (1 + RUN_RAMP_SAFE_PCT / 100.0)
    ramp = run_ramp_rate(weekly_run_km([20.0, safe]))

    assert ramp[1]["change_pct"] == pytest.approx(RUN_RAMP_SAFE_PCT, abs=0.1)
    assert ramp[1]["severity"] == "none"
    assert ramp[1]["threshold_pct"] == RUN_RAMP_SAFE_PCT


def test_an_increase_up_to_double_the_threshold_is_a_caution():
    ramp = run_ramp_rate(weekly_run_km([20.0, 20.0 * (1 + RUN_RAMP_SAFE_PCT * 1.5 / 100.0)]))

    assert ramp[1]["severity"] == "caution"


def test_an_increase_beyond_double_the_threshold_is_high():
    ramp = run_ramp_rate(weekly_run_km([20.0, 20.0 * (1 + RUN_RAMP_SAFE_PCT * 3 / 100.0)]))

    assert ramp[1]["severity"] == "high"
    assert ramp[1]["change_pct"] == pytest.approx(RUN_RAMP_SAFE_PCT * 3, abs=0.1)


def test_cutting_volume_is_reported_but_never_flagged():
    ramp = run_ramp_rate(weekly_run_km([40.0, 16.0]))

    assert ramp[1]["change_pct"] == pytest.approx(-60.0, abs=0.1)
    assert ramp[1]["severity"] == "none"


def test_a_week_following_zero_running_has_no_meaningful_ramp():
    """Anything divided by a zero baseline is an infinite increase, not a signal."""
    acts = run_week(MONDAYS[0], [], start_id=1)
    acts += [make_activity(50, MONDAYS[0], "Ride")]
    acts += run_week(MONDAYS[1], [10.0], start_id=200)
    ramp = run_ramp_rate(weeks_from(acts))

    assert ramp[1]["prev_run_km"] == 0.0
    assert ramp[1]["change_pct"] is None
    assert ramp[1]["severity"] == "unknown"


# ── Spacing ──────────────────────────────────────────────────────────────────


def test_spacing_on_an_empty_history_reports_unknown():
    spacing = run_spacing_profile({})

    assert spacing["severity"] == "unknown"
    assert spacing["total_runs"] == 0
    assert spacing["rest_days_last_week"] is None


def test_too_few_rest_days_is_the_strongest_spacing_signal():
    days = [5.0] * (7 - RUN_MIN_REST_DAYS + 1)
    spacing = run_spacing_profile(weeks_from(run_week(MONDAYS[0], days)))

    assert spacing["rest_days_last_week"] == RUN_MIN_REST_DAYS - 1
    assert spacing["min_rest_days"] == RUN_MIN_REST_DAYS
    assert spacing["severity"] == "high"


def test_stacked_run_days_are_a_caution_even_with_enough_rest():
    # Mon-Tue-Wed on, then four days off: rest days are fine, spacing is not.
    spacing = run_spacing_profile(weeks_from(run_week(MONDAYS[0], [5.0, 5.0, 5.0, 0, 0, 0, 0])))

    assert spacing["rest_days_last_week"] == 4
    assert spacing["back_to_back_days_last_week"] == 2
    assert spacing["max_consecutive_run_days"] == 3
    assert spacing["severity"] == "caution"


def test_well_spread_running_is_not_flagged():
    spacing = run_spacing_profile(weeks_from(run_week(MONDAYS[0], [5.0, 0, 5.0, 0, 5.0, 0, 0])))

    assert spacing["back_to_back_days_last_week"] == 0
    assert spacing["max_consecutive_run_days"] == 1
    assert spacing["longest_gap_days"] == 1
    assert spacing["severity"] == "none"


def test_two_runs_on_the_same_day_cost_only_one_rest_day():
    acts = run_week(MONDAYS[0], [5.0, 0, 0, 0, 0, 0, 0])
    acts.append(make_activity(99, MONDAYS[0], "Run", distance=4000.0))
    spacing = run_spacing_profile(weeks_from(acts))

    assert spacing["total_runs"] == 2
    assert spacing["rest_days_last_week"] == 6


def test_spacing_spans_the_whole_history_but_scores_the_latest_week():
    acts = run_week(MONDAYS[0], [5.0, 5.0, 5.0, 5.0, 0, 0, 0], start_id=1)
    acts += run_week(MONDAYS[1], [5.0, 0, 5.0, 0, 0, 0, 0], start_id=200)
    spacing = run_spacing_profile(weeks_from(acts))

    assert spacing["week_key"] == MONDAYS[1]
    assert spacing["max_consecutive_run_days"] == 4  # from the earlier week
    assert spacing["total_runs"] == 6
    assert spacing["runs_per_week"] == 3.0
    assert spacing["back_to_back_days_last_week"] == 0
    assert spacing["severity"] == "none"


def test_a_week_with_no_running_at_all_is_not_a_spacing_problem():
    spacing = run_spacing_profile(weeks_from([make_activity(1, MONDAYS[0], "Ride")]))

    assert spacing["rest_days_last_week"] == 7
    assert spacing["severity"] == "none"


# ── Long-run share ───────────────────────────────────────────────────────────


def test_long_run_share_on_an_empty_history_reports_unknown():
    assert long_run_share({})["severity"] == "unknown"


def test_a_long_run_within_the_share_limit_is_not_flagged():
    # 4 equal runs → 25% each, comfortably under the limit.
    share = long_run_share(weeks_from(run_week(MONDAYS[0], [5.0, 0, 5.0, 0, 5.0, 0, 5.0])))

    assert share["share"] == pytest.approx(0.25, abs=0.01)
    assert share["max_share"] == RUN_LONG_RUN_MAX_SHARE
    assert share["severity"] == "none"


def test_a_dominant_long_run_is_flagged_high():
    share = long_run_share(weeks_from(run_week(MONDAYS[0], [5.0, 0, 5.0, 0, 0, 0, 25.0])))

    assert share["longest_run_km"] == pytest.approx(25.0)
    assert share["run_km"] == pytest.approx(35.0)
    assert share["share"] > RUN_LONG_RUN_MAX_SHARE
    assert share["severity"] == "high"


def test_a_single_run_week_is_a_spacing_problem_not_a_distribution_one():
    share = long_run_share(weeks_from(run_week(MONDAYS[0], [18.0])))

    assert share["share"] == 1.0
    assert share["severity"] == "caution"


def test_long_run_share_scores_only_the_latest_week():
    acts = run_week(MONDAYS[0], [0, 0, 0, 0, 0, 0, 30.0], start_id=1)
    acts += run_week(MONDAYS[1], [5.0, 0, 5.0, 0, 5.0, 0, 5.0], start_id=200)
    share = long_run_share(weeks_from(acts))

    assert share["week_key"] == MONDAYS[1]
    assert share["severity"] == "none"


def test_a_week_without_running_has_no_long_run_share():
    share = long_run_share(weeks_from([make_activity(1, MONDAYS[0], "Ride")]))

    assert share["share"] is None
    assert share["severity"] == "unknown"


# ── Monotony & strain ────────────────────────────────────────────────────────


def test_a_perfectly_uniform_week_has_no_finite_monotony():
    """Zero deviation would divide by zero; None is reported instead of infinity."""
    weeks = weeks_from(run_week(MONDAYS[0], [5.0] * 7, suffer_score=100))
    latest = training_monotony_and_strain(weeks)["latest"]

    assert latest["monotony"] is None
    assert latest["strain"] is None
    assert latest["severity"] == "unknown"


def test_a_varied_week_scores_low_monotony():
    weeks = weeks_from(run_week(MONDAYS[0], [5.0, 0, 5.0, 0, 0, 0, 0], suffer_score=100))
    latest = training_monotony_and_strain(weeks)["latest"]

    assert latest["total_load"] == pytest.approx(200.0)
    assert latest["monotony"] < MONOTONY_WARN_THRESHOLD
    assert latest["severity"] == "none"


def test_training_every_day_but_one_trips_the_monotony_threshold():
    weeks = weeks_from(run_week(MONDAYS[0], [5.0] * 6 + [0], suffer_score=50))
    latest = training_monotony_and_strain(weeks)["latest"]

    assert latest["monotony"] >= MONOTONY_WARN_THRESHOLD
    assert latest["strain"] < STRAIN_WARN_THRESHOLD
    assert latest["severity"] == "caution"


def test_high_volume_and_high_monotony_together_trip_strain():
    weeks = weeks_from(run_week(MONDAYS[0], [5.0] * 6 + [0], suffer_score=200))
    latest = training_monotony_and_strain(weeks)["latest"]

    assert latest["strain"] >= STRAIN_WARN_THRESHOLD
    assert latest["severity"] == "high"


def test_monotony_counts_every_sport_not_just_running():
    acts = [
        make_activity(1, MONDAYS[0], "Run", suffer_score=100),
        make_activity(2, MONDAYS[0], "Ride", suffer_score=100),
    ]
    latest = training_monotony_and_strain(weeks_from(acts))["latest"]

    assert latest["total_load"] == pytest.approx(200.0)


def test_monotony_is_reported_for_every_week_in_order():
    acts = run_week(MONDAYS[0], [5.0], start_id=1) + run_week(MONDAYS[1], [5.0], start_id=200)
    result = training_monotony_and_strain(weeks_from(acts))

    assert [w["week_key"] for w in result["weeks"]] == MONDAYS[:2]
    assert result["latest"] is result["weeks"][-1]


def test_monotony_on_an_empty_history_has_no_latest_week():
    result = training_monotony_and_strain({})

    assert result["weeks"] == []
    assert result["latest"] is None


# ── Combined assessment ──────────────────────────────────────────────────────


def test_a_well_managed_block_carries_low_risk():
    acts = []
    for i, km in enumerate([20.0, 21.0, 22.0, 23.0]):
        per_run = km / 4
        acts += run_week(MONDAYS[i], [per_run, 0, per_run, 0, per_run, 0, per_run], start_id=100 * (i + 1))
    assessment = assess_run_durability(weeks_from(acts))

    assert assessment["risk_level"] == "low"
    assert assessment["limiters"] == []


def test_stacked_days_and_a_volume_spike_escalate_to_high_risk():
    acts = run_week(MONDAYS[0], [4.0, 0, 4.0, 0, 4.0, 0, 0], start_id=1)
    acts += run_week(MONDAYS[1], [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 0], start_id=200)
    assessment = assess_run_durability(weeks_from(acts))

    assert assessment["risk_level"] == "high"
    assert "run_ramp_rate" in assessment["limiters"]
    assert "run_rest_days" in assessment["limiters"]


def test_every_signal_carries_its_threshold_for_the_dashboard():
    assessment = assess_run_durability(weeks_from(run_week(MONDAYS[0], [5.0, 5.0, 5.0])))

    keys = {s["key"] for s in assessment["signals"]}
    assert {"run_ramp_rate", "run_rest_days", "long_run_share", "training_monotony", "training_strain"} <= keys
    assert all("threshold" in s and "severity" in s for s in assessment["signals"])


def test_the_assessment_carries_no_presentation_values():
    """Colours and prose belong in web/app.js, exactly as ACWR_ZONES does."""
    assessment = assess_run_durability(weeks_from(run_week(MONDAYS[0], [5.0, 5.0, 5.0])))

    assert assessment["risk_level"] in ("low", "moderate", "high", "unknown")
    for signal in assessment["signals"]:
        assert signal["severity"] in ("none", "caution", "high", "unknown")


def test_a_run_acwr_spike_is_folded_into_the_assessment():
    # ACWR scores effort, not distance, so the spike is built with suffer scores.
    acts = []
    for i in range(3):
        acts += run_week(
            MONDAYS[i], [5.0, 0, 5.0, 0, 5.0, 0, 0], start_id=100 * (i + 1), suffer_score=30
        )
    acts += run_week(MONDAYS[3], [5.0, 0, 5.0, 0, 5.0, 0, 0], start_id=900, suffer_score=150)
    weeks = weeks_from(acts)
    acwr_run = calculate_acwr(sorted(weeks), weeks, effort_key="run_relative_effort")

    assessment = assess_run_durability(weeks, acwr_run)
    acwr_signal = next(s for s in assessment["signals"] if s["key"] == "run_acwr")

    assert acwr_signal["zone"] == "spike"
    assert acwr_signal["severity"] == "high"
    assert "run_acwr" in assessment["limiters"]
    assert assessment["risk_level"] == "high"


def test_the_assessment_works_without_an_acwr_map():
    """Short histories have no chronic baseline, but ramp and spacing still apply."""
    assessment = assess_run_durability(weeks_from(run_week(MONDAYS[0], [5.0, 0, 5.0])))

    assert not any(s["key"] == "run_acwr" for s in assessment["signals"])
    assert assessment["risk_level"] in ("low", "moderate", "high", "unknown")


def test_an_empty_history_yields_an_unknown_risk_level():
    assessment = assess_run_durability({})

    assert assessment["risk_level"] == "unknown"
    assert assessment["limiters"] == []


# ── Sport profile ────────────────────────────────────────────────────────────


def test_the_profile_splits_training_across_disciplines(activities):
    profile = sport_strength_profile(activities)

    assert set(profile["groups"]) == {"run", "bike", "swim"}
    assert sum(g["effort_share"] for g in profile["groups"].values()) == pytest.approx(1.0, abs=0.01)
    assert profile["groups"]["run"]["sessions"] == 3
    assert profile["most_trained"] == "run"


def test_the_profile_reports_pace_in_the_unit_each_sport_uses(activities):
    profile = sport_strength_profile(activities)

    assert "avg_pace_sec_per_km" in profile["groups"]["run"]
    assert "avg_pace_sec_per_100m" in profile["groups"]["swim"]
    assert "avg_pace_sec_per_km" not in profile["groups"]["swim"]


def test_run_pace_is_referenced_against_the_configured_10k_pb(activities):
    profile = sport_strength_profile(activities)

    assert profile["run_pb_pace_sec_per_km"] == pytest.approx(ATHLETE_PB_10K_SEC / 10.0, abs=0.1)
    assert profile["pb_reference_sec"]["run_10k"] == ATHLETE_PB_10K_SEC
    # The ratio is training pace measured against the athlete's own race pace.
    expected = profile["groups"]["run"]["avg_pace_sec_per_km"] / (ATHLETE_PB_10K_SEC / 10.0)
    assert profile["run_pb_ratio"] == pytest.approx(expected, abs=0.001)


def test_the_profile_survives_an_empty_activity_list():
    profile = sport_strength_profile([])

    assert profile["groups"] == {}
    assert profile["most_trained"] is None
    assert profile["run_pb_ratio"] is None


# ── Cross-training substitution ──────────────────────────────────────────────


def test_low_risk_means_the_whole_target_can_be_run():
    plan = suggest_cross_training(300.0, {"risk_level": "low"})

    assert plan["safe_run_load"] == 300.0
    assert plan["shortfall_load"] == 0.0
    assert all(s["replacement_load"] == 0.0 for s in plan["substitutions"])


def test_high_risk_moves_most_of_the_shortfall_to_aqua_jogging():
    plan = suggest_cross_training(300.0, {"risk_level": "high"})

    assert plan["safe_run_load"] < 300.0
    assert plan["shortfall_load"] == pytest.approx(300.0 - plan["safe_run_load"], abs=0.1)

    aqua, bike = plan["substitutions"]
    assert aqua["modality"] == "aqua_jog"
    assert bike["modality"] == "bike"
    assert aqua["replacement_load"] > bike["replacement_load"]
    assert aqua["replacement_load"] + bike["replacement_load"] == pytest.approx(
        plan["shortfall_load"], abs=0.1
    )


def test_the_substitution_factors_come_from_config():
    plan = suggest_cross_training(100.0, {"risk_level": "high"})
    factors = {s["modality"]: s["load_factor"] for s in plan["substitutions"]}

    assert factors["aqua_jog"] == AQUA_JOG_LOAD_FACTOR
    assert factors["bike"] == BIKE_RUN_LOAD_FACTOR


def test_riskier_running_means_less_running(activities):
    profile = sport_strength_profile(activities)
    loads = {
        risk: suggest_cross_training(300.0, {"risk_level": risk}, profile)["safe_run_load"]
        for risk in ("low", "moderate", "high")
    }

    assert loads["low"] > loads["moderate"] > loads["high"]


def test_minutes_are_derived_from_the_athletes_own_effort_rate(activities):
    profile = sport_strength_profile(activities)
    plan = suggest_cross_training(300.0, {"risk_level": "high"}, profile)

    bike = next(s for s in plan["substitutions"] if s["modality"] == "bike")
    expected = round(
        bike["replacement_load"]
        / (profile["groups"]["bike"]["effort_per_min"] * BIKE_RUN_LOAD_FACTOR)
    )
    assert bike["equivalent_minutes"] == expected


def test_without_history_the_load_is_still_reported_but_minutes_are_not():
    plan = suggest_cross_training(300.0, {"risk_level": "high"})

    for sub in plan["substitutions"]:
        assert sub["replacement_load"] > 0
        assert sub["equivalent_minutes"] is None


def test_the_strength_prescription_is_single_leg_and_scales_with_risk():
    low = suggest_cross_training(300.0, {"risk_level": "low"})["strength"]
    high = suggest_cross_training(300.0, {"risk_level": "high"})["strength"]

    assert low["focus"] == "single_leg"
    assert high["sessions_per_week"] > low["sessions_per_week"]
    assert high["exercises"] == list(SINGLE_LEG_EXERCISES)


def test_the_plan_carries_the_limiters_it_is_responding_to():
    durability = {"risk_level": "high", "limiters": ["run_ramp_rate", "run_rest_days"]}
    plan = suggest_cross_training(300.0, durability)

    assert plan["limiters"] == ["run_ramp_rate", "run_rest_days"]
    assert plan["risk_level"] == "high"


def test_a_negative_target_is_clamped_rather_than_inverted():
    plan = suggest_cross_training(-50.0, {"risk_level": "high"})

    assert plan["target_run_load"] == 0.0
    assert plan["shortfall_load"] == 0.0
