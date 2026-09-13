"""Training-load metric tests: relative effort, weekly rollups, and ACWR."""

from src.config import ACWR_CHRONIC_WEEKS, HR_MAX, HR_REST
from src.analytics.metrics import (
    build_progression_history,
    calculate_acwr,
    calculate_relative_effort,
    normalize_rpe,
    process_activities_into_weeks,
)


def _act(day: str, sport: str, **kwargs) -> dict:
    act = {
        "id": abs(hash((day, sport, kwargs.get("moving_time", 0)))) % 10**9,
        "start_date_local": f"{day}T08:00:00Z",
        "sport_type": sport,
        "distance": 0.0,
        "moving_time": 3600,
        "average_speed": 0.0,
        "total_elevation_gain": 0.0,
    }
    act.update(kwargs)
    return act


# Relative Effort


def test_strava_suffer_score_wins_when_present():
    assert calculate_relative_effort({"suffer_score": 87, "moving_time": 3600}) == 87.0


def test_trimp_estimate_used_without_suffer_score():
    act = {"moving_time": 3600, "average_heartrate": (HR_MAX + HR_REST) / 2}
    effort = calculate_relative_effort(act)
    # A one-hour effort at half heart-rate reserve should be a substantial score,
    # not the flat duration fallback of 30.
    assert effort > 30.0


def test_relative_effort_rises_with_heart_rate():
    low = calculate_relative_effort({"moving_time": 3600, "average_heartrate": HR_REST + 40})
    high = calculate_relative_effort({"moving_time": 3600, "average_heartrate": HR_MAX - 5})
    assert high > low


def test_duration_fallback_without_heart_rate():
    assert calculate_relative_effort({"moving_time": 3600}) == 30.0
    assert calculate_relative_effort({"moving_time": 0}) == 0.0


# Weekly rollups


def test_activities_group_into_monday_weeks():
    # 2026-08-24 is a Monday; 2026-08-30 the Sunday of the same week.
    weeks = process_activities_into_weeks([
        _act("2026-08-24", "Run"),
        _act("2026-08-30", "Run"),
        _act("2026-08-31", "Run"),  # next Monday
    ])
    assert sorted(weeks) == ["2026-08-24", "2026-08-31"]
    assert len(weeks["2026-08-24"]["activities"]) == 2
    assert weeks["2026-08-24"]["week_sunday"] == "2026-08-30"


def test_weekly_volumes_split_by_sport_with_corrections():
    weeks = process_activities_into_weeks([
        _act("2026-08-24", "Run", distance=10000.0, moving_time=3000, total_elevation_gain=120.0),
        _act("2026-08-25", "Ride", distance=40000.0, moving_time=5400, total_elevation_gain=300.0),
        _act("2026-08-26", "Swim", distance=3000.0, moving_time=2700),
        _act("2026-08-27", "WeightTraining", moving_time=1800),
    ])
    w = weeks["2026-08-24"]
    assert round(w["run_dist_km"], 2) == 10.0
    assert round(w["bike_dist_km"], 2) == 40.0
    assert w["swim_dist_m"] == 1500.0  # divisor applied
    assert w["strength_time_sec"] == 1800
    assert w["total_time_sec"] == 3000 + 5400 + 2700 + 1800
    assert w["total_elevation_m"] == 420.0


def test_week_activities_are_sorted_chronologically():
    weeks = process_activities_into_weeks([_act("2026-08-26", "Run"), _act("2026-08-24", "Run")])
    dates = [a["start_date_local"] for a in weeks["2026-08-24"]["activities"]]
    assert dates == sorted(dates)


# ACWR


def _weeks(efforts: list[float]) -> tuple[list[str], dict]:
    """Build N consecutive weeks with the given relative-effort totals."""
    keys = [f"2026-0{1 + i // 4}-{1 + 7 * (i % 4):02d}" for i in range(len(efforts))]
    return keys, {k: {"total_relative_effort": e} for k, e in zip(keys, efforts)}


def test_no_ratio_until_enough_chronic_history():
    keys, weeks = _weeks([100.0, 100.0, 100.0])
    acwr = calculate_acwr(keys, weeks)
    assert acwr[keys[0]]["acwr_ratio"] is None
    assert acwr[keys[0]]["chronic_weeks"] == 0
    assert acwr[keys[0]]["zone"] == "unknown"
    assert acwr[keys[1]]["acwr_ratio"] is None  # only 1 prior week
    assert acwr[keys[2]]["acwr_ratio"] == 1.0  # 2 prior weeks is enough


def test_chronic_window_excludes_the_current_week():
    """
    The current week must not enter its own chronic baseline: a doubled week
    should read as 2.0, not the compressed value self-inclusion produces.
    """
    keys, weeks = _weeks([100.0, 100.0, 100.0, 100.0, 200.0])
    acwr = calculate_acwr(keys, weeks)
    assert acwr[keys[4]]["chronic_effort"] == 100.0
    assert acwr[keys[4]]["acwr_ratio"] == 2.0


def test_chronic_window_is_bounded_to_configured_weeks():
    efforts = [10.0] * 10 + [100.0] * ACWR_CHRONIC_WEEKS + [100.0]
    keys, weeks = _weeks(efforts)
    acwr = calculate_acwr(keys, weeks)
    # The distant 10.0 weeks fall outside the window, so the baseline is 100.
    assert acwr[keys[-1]]["chronic_weeks"] == ACWR_CHRONIC_WEEKS
    assert acwr[keys[-1]]["chronic_effort"] == 100.0
    assert acwr[keys[-1]]["acwr_ratio"] == 1.0


def test_acwr_zones():
    def zone_for(current: float) -> str:
        keys, weeks = _weeks([100.0, 100.0, 100.0, current])
        return calculate_acwr(keys, weeks)[keys[3]]["zone"]

    assert zone_for(50.0) == "low"
    assert zone_for(100.0) == "optimal"
    assert zone_for(140.0) == "overreaching"
    assert zone_for(200.0) == "spike"


def test_zero_chronic_load_yields_no_ratio_instead_of_dividing_by_zero():
    keys, weeks = _weeks([0.0, 0.0, 0.0, 150.0])
    acwr = calculate_acwr(keys, weeks)
    assert acwr[keys[3]]["acwr_ratio"] is None
    assert acwr[keys[3]]["zone"] == "unknown"


# Progression


def test_progression_reuses_a_supplied_acwr_map():
    weeks = process_activities_into_weeks([
        _act("2026-08-24", "Run", distance=10000.0, moving_time=3600, suffer_score=100),
    ])
    sentinel = {"2026-08-24": {"acwr_ratio": 4.2, "zone": "spike"}}
    progression = build_progression_history(weeks, acwr_map=sentinel)
    assert progression[0]["acwr"]["acwr_ratio"] == 4.2
    assert progression[0]["label"] == "24/8"
    assert progression[0]["total_hours"] == 1.0


def test_progression_computes_acwr_when_not_supplied():
    weeks = process_activities_into_weeks([_act("2026-08-24", "Run", moving_time=3600)])
    progression = build_progression_history(weeks)
    assert "acwr" in progression[0]


# normalize_rpe


def test_normalize_rpe_explicit_perceived_exertion():
    assert normalize_rpe({}, {"perceived_exertion": 8}) == 8
    assert normalize_rpe({"perceived_exertion": 6}, {}) == 6
    assert normalize_rpe({}, {"perceived_exertion": 11}) == 10  # clamped to 10
    assert normalize_rpe({}, {"perceived_exertion": 0}) == 4   # invalid -> fallback


def test_normalize_rpe_from_heart_rate():
    # HR_MAX=185, HR_REST=50
    # Recovery / light walk (HR 98, ~35% HRR) -> 1-2
    assert normalize_rpe({"average_heartrate": 98}) in (1, 2)
    # Weights / recovery (HR 117, ~50% HRR) -> 3
    assert normalize_rpe({"average_heartrate": 117}) == 3
    # Easy Z2 ride (HR 131, ~60% HRR) -> 4
    assert normalize_rpe({"average_heartrate": 131}) == 4
    # Tempo run (HR 154, ~77% HRR) -> 6 or 7
    assert normalize_rpe({"average_heartrate": 154}) in (6, 7)
    # Hard run (HR 164, ~84% HRR) -> 8
    assert normalize_rpe({"average_heartrate": 164}) == 8
    # All-out / VO2max (HR 176, ~93% HRR) -> 9 or 10
    assert normalize_rpe({"average_heartrate": 176}) in (9, 10)


def test_normalize_rpe_from_suffer_score():
    # When HR is absent, suffer score per minute intensity is used
    # 45m workout with suffer_score=72 -> intensity 1.6 -> 9-10
    assert normalize_rpe({"moving_time": 2700}, {"suffer_score": 72}) in (9, 10)
    # 53m workout with suffer_score=11 -> intensity 0.21 -> 2-3
    assert normalize_rpe({"moving_time": 3180}, {"suffer_score": 11}) in (2, 3)


def test_normalize_rpe_fallback_to_moderate():
    assert normalize_rpe({}, {}) == 4

