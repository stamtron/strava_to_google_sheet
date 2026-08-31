"""
Run durability and cross-sport substitution analytics.

Aggregate training load hides the thing an impact-sensitive athlete needs to
see. A week where run volume halves and bike volume doubles shows a flat total
and a comfortable ACWR, while the tissue actually being loaded — tendon, bone,
fascia — has just had its stimulus yanked around. Everything here looks at
running on its own, and at the *shape* of the week rather than only its size:
how fast volume is ramping, whether run days are stacked back to back, how much
of the week sits in one long run, and how monotonous the daily load is.

Nothing in this module talks to a network or a model. It computes numbers; the
AI coach explains them and the dashboard colours them. Following the convention
already set by `ACWR_ZONES`, every function returns machine-readable keys —
`severity`, `risk_level`, exercise identifiers — and never prose, colours, or
emoji.
"""

from datetime import datetime, timedelta

from src.config import (
    AQUA_JOG_LOAD_FACTOR,
    BIKE_RUN_LOAD_FACTOR,
    MONOTONY_WARN_THRESHOLD,
    RUN_LONG_RUN_MAX_SHARE,
    RUN_MIN_REST_DAYS,
    RUN_RAMP_SAFE_PCT,
    STRAIN_WARN_THRESHOLD,
)
from src.analytics.metrics import calculate_relative_effort, sport_group
from src.formatting import corrected_distance_and_speed

# Severity is ordered, so the worst signal in a set can be found by max().
SEVERITY_ORDER = ("none", "unknown", "caution", "high")

# Impact-free single-leg work. These are identifiers, not display strings: the
# frontend and the chat agent map them to names, cues, and video searches.
SINGLE_LEG_EXERCISES = (
    "single_leg_calf_raise",
    "split_squat",
    "step_down",
    "single_leg_bridge",
    "hip_abduction_side_lying",
    "single_leg_balance_reach",
)


def _severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else 0


def _worst(severities) -> str:
    """The most serious severity in a collection, or "none" when empty."""
    return max(severities, key=_severity_rank, default="none")


def _act_date(act: dict):
    raw = act.get("start_date_local", "")
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()


def _run_activities(week: dict) -> list[dict]:
    return [
        a
        for a in week.get("activities", [])
        if sport_group(a.get("sport_type") or a.get("type", "")) == "run"
    ]


def run_ramp_rate(weeks_dict: dict) -> list[dict]:
    """
    Week-over-week change in run volume, scored against the safe ramp threshold.

    The first week has no predecessor and weeks following a zero-run week have no
    meaningful denominator, so both report `change_pct=None` rather than an
    infinite or invented increase. Reductions are reported but never flagged:
    cutting volume is not an injury mechanism.
    """
    results = []
    prev_km = None

    for w_key in sorted(weeks_dict.keys()):
        run_km = round(weeks_dict[w_key].get("run_dist_km", 0.0), 2)

        if prev_km is None or prev_km <= 0:
            change_pct, severity = None, "unknown"
        else:
            change_pct = round((run_km - prev_km) / prev_km * 100.0, 1)
            if change_pct <= RUN_RAMP_SAFE_PCT:
                severity = "none"
            elif change_pct <= RUN_RAMP_SAFE_PCT * 2:
                severity = "caution"
            else:
                severity = "high"

        results.append({
            "week_key": w_key,
            "run_km": run_km,
            "prev_run_km": prev_km,
            "change_pct": change_pct,
            "threshold_pct": RUN_RAMP_SAFE_PCT,
            "severity": severity,
        })
        prev_km = run_km

    return results


def run_spacing_profile(weeks_dict: dict) -> dict:
    """
    How run days are distributed, over the whole span and in the latest week.

    Two athletes running 40 km a week carry very different risk if one spreads it
    over five days and the other stacks it into three consecutive ones. Rest days
    are counted as calendar days in the week without a run, so a day with two
    runs still costs only one.
    """
    if not weeks_dict:
        return {
            "week_key": None,
            "total_runs": 0,
            "runs_per_week": 0.0,
            "max_consecutive_run_days": 0,
            "longest_gap_days": 0,
            "rest_days_last_week": None,
            "back_to_back_days_last_week": 0,
            "min_rest_days": RUN_MIN_REST_DAYS,
            "severity": "unknown",
        }

    sorted_keys = sorted(weeks_dict.keys())
    all_run_dates = sorted({
        d
        for w_key in sorted_keys
        for a in _run_activities(weeks_dict[w_key])
        if (d := _act_date(a)) is not None
    })
    total_runs = sum(len(_run_activities(weeks_dict[k])) for k in sorted_keys)

    max_streak = streak = 0
    longest_gap = 0
    for i, day in enumerate(all_run_dates):
        if i and (day - all_run_dates[i - 1]).days == 1:
            streak += 1
        else:
            streak = 1
        max_streak = max(max_streak, streak)
        if i:
            longest_gap = max(longest_gap, (day - all_run_dates[i - 1]).days - 1)

    latest_key = sorted_keys[-1]
    latest_dates = sorted({
        d for a in _run_activities(weeks_dict[latest_key]) if (d := _act_date(a)) is not None
    })
    rest_days = 7 - len(latest_dates)
    back_to_back = sum(
        1 for i in range(1, len(latest_dates)) if (latest_dates[i] - latest_dates[i - 1]).days == 1
    )

    if not latest_dates:
        severity = "none"
    elif rest_days < RUN_MIN_REST_DAYS:
        severity = "high"
    elif back_to_back >= 2:
        severity = "caution"
    else:
        severity = "none"

    return {
        "week_key": latest_key,
        "total_runs": total_runs,
        "runs_per_week": round(total_runs / len(sorted_keys), 2),
        "max_consecutive_run_days": max_streak,
        "longest_gap_days": longest_gap,
        "rest_days_last_week": rest_days,
        "back_to_back_days_last_week": back_to_back,
        "min_rest_days": RUN_MIN_REST_DAYS,
        "severity": severity,
    }


def long_run_share(weeks_dict: dict) -> dict:
    """
    The latest week's longest run as a fraction of that week's run volume.

    Past roughly 40% of the week, the long run stops being one stimulus among
    several and becomes the week's dominant injury exposure — most of the load,
    accumulated in a single session, on already-fatigued tissue.
    """
    if not weeks_dict:
        return {
            "week_key": None,
            "longest_run_km": 0.0,
            "run_km": 0.0,
            "share": None,
            "max_share": RUN_LONG_RUN_MAX_SHARE,
            "severity": "unknown",
        }

    week_key = max(weeks_dict.keys())
    runs = _run_activities(weeks_dict[week_key])
    distances = [corrected_distance_and_speed(a)[0] / 1000.0 for a in runs]
    longest = max(distances, default=0.0)
    run_km = weeks_dict[week_key].get("run_dist_km", 0.0)

    if run_km <= 0:
        share, severity = None, "unknown"
    else:
        share = round(longest / run_km, 3)
        if share <= RUN_LONG_RUN_MAX_SHARE:
            severity = "none"
        # A single run in the week is trivially 100% of it. That is a spacing
        # problem, already reported as such, not a long-run-distribution one.
        elif len(runs) <= 1:
            severity = "caution"
        else:
            severity = "high"

    return {
        "week_key": week_key,
        "longest_run_km": round(longest, 2),
        "run_km": round(run_km, 2),
        "share": share,
        "max_share": RUN_LONG_RUN_MAX_SHARE,
        "severity": severity,
    }


def training_monotony_and_strain(weeks_dict: dict) -> dict:
    """
    Foster's monotony and strain, per week, across all sports.

    Monotony is the mean daily load divided by its standard deviation over the
    seven calendar days, zeros included. A week of seven identical moderate days
    scores worse than one with three hard days and four off, because it never
    lets the athlete recover. Strain multiplies the week's total load by its
    monotony, so it catches high volume and unvarying volume together.

    A week whose days are perfectly uniform has zero deviation and therefore no
    finite monotony; it reports None rather than infinity.
    """
    per_week = []

    for w_key in sorted(weeks_dict.keys()):
        week = weeks_dict[w_key]
        monday = datetime.fromisoformat(week["week_monday"]).date()
        daily = {monday + timedelta(days=i): 0.0 for i in range(7)}

        for act in week.get("activities", []):
            day = _act_date(act)
            if day in daily:
                daily[day] += calculate_relative_effort(act)

        loads = list(daily.values())
        total = sum(loads)
        mean = total / 7.0
        variance = sum((x - mean) ** 2 for x in loads) / 7.0
        sd = variance**0.5

        if sd <= 0:
            monotony, strain, severity = None, None, "unknown"
        else:
            monotony = round(mean / sd, 2)
            strain = round(total * monotony, 1)
            if monotony >= MONOTONY_WARN_THRESHOLD or strain >= STRAIN_WARN_THRESHOLD:
                severity = "high" if strain >= STRAIN_WARN_THRESHOLD else "caution"
            else:
                severity = "none"

        per_week.append({
            "week_key": w_key,
            "total_load": round(total, 1),
            "monotony": monotony,
            "strain": strain,
            "monotony_threshold": MONOTONY_WARN_THRESHOLD,
            "strain_threshold": STRAIN_WARN_THRESHOLD,
            "severity": severity,
        })

    return {"weeks": per_week, "latest": per_week[-1] if per_week else None}


def assess_run_durability(weeks_dict: dict, acwr_run: dict | None = None) -> dict:
    """
    Combine the individual run-load signals into one assessment.

    `acwr_run` is the output of `calculate_acwr(..., effort_key="run_relative_effort")`.
    It is optional so the assessment still works on a short history, where ACWR
    has no chronic baseline to compare against but ramp rate and spacing do.

    Returns `signals` — every measurement with its threshold and severity, so the
    dashboard can show the full picture — and `limiters`, just the signal keys
    that are actually flagged, which is what the coach acts on.
    """
    ramp = run_ramp_rate(weeks_dict)
    spacing = run_spacing_profile(weeks_dict)
    long_run = long_run_share(weeks_dict)
    monotony = training_monotony_and_strain(weeks_dict)

    latest_ramp = ramp[-1] if ramp else None
    latest_monotony = monotony["latest"]

    signals = []

    if latest_ramp:
        signals.append({
            "key": "run_ramp_rate",
            "value": latest_ramp["change_pct"],
            "threshold": RUN_RAMP_SAFE_PCT,
            "severity": latest_ramp["severity"],
        })

    signals.append({
        "key": "run_rest_days",
        "value": spacing["rest_days_last_week"],
        "threshold": RUN_MIN_REST_DAYS,
        "severity": spacing["severity"],
    })

    signals.append({
        "key": "long_run_share",
        "value": long_run["share"],
        "threshold": RUN_LONG_RUN_MAX_SHARE,
        "severity": long_run["severity"],
    })

    if latest_monotony:
        signals.append({
            "key": "training_monotony",
            "value": latest_monotony["monotony"],
            "threshold": MONOTONY_WARN_THRESHOLD,
            "severity": latest_monotony["severity"],
        })
        signals.append({
            "key": "training_strain",
            "value": latest_monotony["strain"],
            "threshold": STRAIN_WARN_THRESHOLD,
            "severity": (
                "high"
                if latest_monotony["strain"] is not None
                and latest_monotony["strain"] >= STRAIN_WARN_THRESHOLD
                else "none" if latest_monotony["strain"] is not None else "unknown"
            ),
        })

    if acwr_run and weeks_dict:
        latest = acwr_run.get(max(weeks_dict.keys()), {})
        ratio = latest.get("acwr_ratio")
        zone = latest.get("zone", "unknown")
        signals.append({
            "key": "run_acwr",
            "value": ratio,
            "threshold": 1.3,
            "severity": {
                "optimal": "none",
                "low": "none",
                "overreaching": "caution",
                "spike": "high",
            }.get(zone, "unknown"),
            "zone": zone,
        })

    severities = [s["severity"] for s in signals]
    worst = _worst(severities)
    caution_count = severities.count("caution")

    if worst == "high":
        risk_level = "high"
    elif caution_count >= 2:
        risk_level = "moderate"
    elif caution_count == 1:
        risk_level = "low"
    elif all(s in ("unknown", "none") for s in severities) and "none" not in severities:
        risk_level = "unknown"
    else:
        risk_level = "low"

    return {
        "risk_level": risk_level,
        "signals": signals,
        "limiters": [s["key"] for s in signals if s["severity"] in ("caution", "high")],
        "ramp_history": ramp,
        "spacing": spacing,
        "long_run": long_run,
        "monotony": monotony,
    }


def sport_strength_profile(activities: list[dict]) -> dict:
    """
    How the athlete's training is distributed across disciplines, plus a run
    performance reference.

    `training_share` is deliberately named for what it measures. Config holds
    verified PBs for running and for triathlon as a whole, but no swim or bike
    split, so there is no honest basis for ranking raw ability across the three
    — only for saying where the training time currently goes, and how recent
    running compares to the athlete's own race pace.
    """
    from src.config import (
        ATHLETE_PB_5K_SEC,
        ATHLETE_PB_10K_SEC,
        ATHLETE_PB_HALF_MARATHON_SEC,
        ATHLETE_PB_OLYMPIC_TRI_SEC,
        ATHLETE_PB_SPRINT_TRI_SEC,
    )

    groups: dict[str, dict] = {}
    for act in activities:
        group = sport_group(act.get("sport_type") or act.get("type", ""))
        g = groups.setdefault(
            group, {"sessions": 0, "time_sec": 0, "effort": 0.0, "distance_m": 0.0}
        )
        dist_m, _ = corrected_distance_and_speed(act)
        g["sessions"] += 1
        g["time_sec"] += act.get("moving_time", 0) or 0
        g["effort"] += calculate_relative_effort(act)
        g["distance_m"] += dist_m

    total_effort = sum(g["effort"] for g in groups.values())
    total_time = sum(g["time_sec"] for g in groups.values())

    for group, g in groups.items():
        g["effort"] = round(g["effort"], 1)
        g["distance_km"] = round(g["distance_m"] / 1000.0, 2)
        g["effort_share"] = round(g["effort"] / total_effort, 3) if total_effort else 0.0
        g["time_share"] = round(g["time_sec"] / total_time, 3) if total_time else 0.0
        g["effort_per_min"] = round(g["effort"] / (g["time_sec"] / 60.0), 3) if g["time_sec"] else None

        if group in ("run", "bike") and g["distance_m"] > 0 and g["time_sec"] > 0:
            g["avg_pace_sec_per_km"] = round(g["time_sec"] / (g["distance_m"] / 1000.0), 1)
        elif group == "swim" and g["distance_m"] > 0 and g["time_sec"] > 0:
            g["avg_pace_sec_per_100m"] = round(g["time_sec"] / (g["distance_m"] / 100.0), 1)
        g.pop("distance_m")

    # Recent easy/steady running against the athlete's own 10K race pace. Above
    # 1.0 means training pace is slower than race pace, which is expected —
    # it is the size of the gap that says whether running is in a good place.
    pb_run_pace = ATHLETE_PB_10K_SEC / 10.0
    run_pace = groups.get("run", {}).get("avg_pace_sec_per_km")
    run_pb_ratio = round(run_pace / pb_run_pace, 3) if run_pace else None

    ranked = sorted(groups.items(), key=lambda kv: kv[1]["effort_share"], reverse=True)

    return {
        "groups": groups,
        "most_trained": ranked[0][0] if ranked else None,
        "least_trained": ranked[-1][0] if ranked else None,
        "run_pb_pace_sec_per_km": round(pb_run_pace, 1),
        "run_pb_ratio": run_pb_ratio,
        "pb_reference_sec": {
            "run_5k": ATHLETE_PB_5K_SEC,
            "run_10k": ATHLETE_PB_10K_SEC,
            "run_half_marathon": ATHLETE_PB_HALF_MARATHON_SEC,
            "triathlon_sprint": ATHLETE_PB_SPRINT_TRI_SEC,
            "triathlon_olympic": ATHLETE_PB_OLYMPIC_TRI_SEC,
        },
    }


# How much of a target run stimulus to actually run, by durability risk. The
# remainder is bought impact-free rather than skipped, so aerobic fitness keeps
# climbing while the tissue that hurts gets a lighter week.
_RUN_RETENTION_BY_RISK = {"low": 1.0, "moderate": 0.8, "high": 0.6, "unknown": 0.9}


def suggest_cross_training(
    target_run_load: float,
    durability: dict,
    profile: dict | None = None,
) -> dict:
    """
    Turn a desired weekly run stimulus into a plan the athlete's legs can absorb.

    This is the answer to "I'm strong on the bike and in the water, I want to run
    more, and running hurts": keep the aerobic dose, move part of it off the
    pavement. The shortfall between what was wanted and what is currently safe to
    run is split between aqua jogging — which reproduces the running motion
    almost exactly — and cycling, which needs more minutes for the same dose.

    `equivalent_minutes` is derived from the athlete's own observed effort per
    minute in each modality, via `profile`. Without a profile, or without history
    in that sport, the load is still reported and the minutes are None rather
    than guessed from a population average.
    """
    risk = durability.get("risk_level", "unknown")
    retention = _RUN_RETENTION_BY_RISK.get(risk, 0.9)

    target = max(0.0, float(target_run_load))
    safe_run_load = round(target * retention, 1)
    shortfall = round(target - safe_run_load, 1)

    groups = (profile or {}).get("groups", {})
    run_per_min = (groups.get("run") or {}).get("effort_per_min")
    bike_per_min = (groups.get("bike") or {}).get("effort_per_min")

    def _minutes(replacement_load: float, per_min, factor: float):
        if not per_min or replacement_load <= 0:
            return None
        return round(replacement_load / (per_min * factor))

    # Aqua jogging takes the larger share: it is the closest neuromuscular match
    # to running, so it protects run-specific fitness the bike cannot.
    aqua_load = round(shortfall * 0.6, 1)
    bike_load = round(shortfall - aqua_load, 1)

    substitutions = [
        {
            "modality": "aqua_jog",
            "load_factor": AQUA_JOG_LOAD_FACTOR,
            "replacement_load": aqua_load,
            # No aqua-jog history exists, so the run rate stands in for it.
            "equivalent_minutes": _minutes(aqua_load, run_per_min, AQUA_JOG_LOAD_FACTOR),
        },
        {
            "modality": "bike",
            "load_factor": BIKE_RUN_LOAD_FACTOR,
            "replacement_load": bike_load,
            "equivalent_minutes": _minutes(bike_load, bike_per_min, BIKE_RUN_LOAD_FACTOR),
        },
    ]

    return {
        "risk_level": risk,
        "target_run_load": round(target, 1),
        "safe_run_load": safe_run_load,
        "shortfall_load": shortfall,
        "run_retention": retention,
        "substitutions": substitutions,
        "strength": {
            "sessions_per_week": 3 if risk == "high" else 2,
            "focus": "single_leg",
            "exercises": list(SINGLE_LEG_EXERCISES),
        },
        "limiters": durability.get("limiters", []),
    }
