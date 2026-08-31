"""
Training Load, Relative Effort (Suffer Score), and Progression Metrics.
"""

import math
from datetime import datetime, timedelta

from src.config import (
    ACWR_CHRONIC_WEEKS,
    ACWR_MIN_CHRONIC_WEEKS,
    BIKE_SPORTS,
    HR_MAX,
    HR_REST,
    RUN_SPORTS,
    STRENGTH_SPORTS,
    SWIM_SPORTS,
)
from src.formatting import corrected_distance_and_speed

# ACWR interpretation bands. `zone` is a stable machine-readable key; the
# frontend maps it to a colour so presentation stays out of the analytics layer.
ACWR_ZONES = (
    (0.8, "low", "Low Load (Recovery / Undertraining)"),
    (1.3, "optimal", "Optimal Zone"),
    (1.5, "overreaching", "Overreaching"),
    (float("inf"), "spike", "Overtraining Spike Risk"),
)


def calculate_relative_effort(act: dict) -> float:
    """
    Return Strava's suffer score, or estimate a TRIMP-style score from duration
    and heart rate when Strava has none.
    """
    suffer = act.get("suffer_score")
    if suffer is not None and suffer > 0:
        return float(suffer)

    moving_time_min = (act.get("moving_time", 0) or 0) / 60.0
    avg_hr = act.get("average_heartrate")
    if moving_time_min > 0 and avg_hr and HR_MAX > HR_REST:
        # Banister TRIMP: fraction of heart-rate reserve, exponentially weighted.
        hr_ratio = max(0.4, min(1.0, (avg_hr - HR_REST) / (HR_MAX - HR_REST)))
        stress_factor = hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)
        return round(moving_time_min * stress_factor, 1)

    # Fallback duration-based load
    return round(moving_time_min * 0.5, 1) if moving_time_min > 0 else 0.0


def sport_group(sport: str) -> str:
    """
    Map a Strava sport type to its analytics group.

    Returns a stable key — "run", "bike", "swim", "strength" or "other" — that is
    also the prefix of the corresponding per-sport week fields.
    """
    if sport in RUN_SPORTS:
        return "run"
    if sport in BIKE_SPORTS:
        return "bike"
    if sport in SWIM_SPORTS:
        return "swim"
    if sport in STRENGTH_SPORTS:
        return "strength"
    return "other"


# Per-sport effort field names, in the order they are reported.
SPORT_GROUPS = ("run", "bike", "swim", "strength", "other")
EFFORT_KEYS = {g: f"{g}_relative_effort" for g in SPORT_GROUPS}


def calculate_relative_effort_by_sport(activities: list[dict]) -> dict[str, float]:
    """
    Split relative effort across sport groups.

    Weekly effort has only ever been reported as a single total, even though
    volume is already split per sport. An athlete who halves their running while
    doubling their cycling shows a flat total, which is precisely the shift a
    load-management view needs to surface.
    """
    totals = {g: 0.0 for g in SPORT_GROUPS}
    for act in activities:
        group = sport_group(act.get("sport_type") or act.get("type", ""))
        totals[group] += calculate_relative_effort(act)
    return {g: round(v, 1) for g, v in totals.items()}


def process_activities_into_weeks(activities: list[dict]) -> dict:
    """
    Group activities by week (Monday to Sunday) and calculate all sport volumes,
    elevation gains, calories, and Relative Effort (Suffer Score).

    Distances go through `corrected_distance_and_speed`, which applies the swim
    divisor and the indoor-trainer distance estimate.
    """
    weeks = {}

    for act in activities:
        raw_dt = act.get("start_date_local", "")
        if not raw_dt:
            continue
        act_date = datetime.fromisoformat(raw_dt.replace("Z", "+00:00")).date()
        week_monday = act_date - timedelta(days=act_date.weekday())
        week_key = week_monday.isoformat()

        if week_key not in weeks:
            weeks[week_key] = {
                "week_monday": week_key,
                "week_sunday": (week_monday + timedelta(days=6)).isoformat(),
                "activities": [],
                "run_dist_km": 0.0,
                "run_time_sec": 0,
                "run_elev_m": 0.0,
                "bike_dist_km": 0.0,
                "bike_time_sec": 0,
                "bike_elev_m": 0.0,
                "swim_dist_m": 0.0,
                "swim_time_sec": 0,
                "strength_time_sec": 0,
                "total_time_sec": 0,
                "total_elevation_m": 0.0,
                "total_calories": 0.0,
                "total_relative_effort": 0.0,
                **{key: 0.0 for key in EFFORT_KEYS.values()},
            }

        week = weeks[week_key]
        week["activities"].append(act)

        moving_time = act.get("moving_time", 0) or 0
        dist_m, _ = corrected_distance_and_speed(act)
        dist_km = dist_m / 1000.0
        elev = float(act.get("total_elevation_gain", 0) or 0)
        sport = act.get("sport_type") or act.get("type", "")
        calories = float(act.get("calories", 0) or (act.get("kilojoules", 0) * 0.239) or 0)

        effort = calculate_relative_effort(act)
        group = sport_group(sport)

        week["total_time_sec"] += moving_time
        week["total_elevation_m"] += elev
        week["total_calories"] += calories
        week["total_relative_effort"] += effort
        week[EFFORT_KEYS[group]] += effort

        if group == "run":
            week["run_dist_km"] += dist_km
            week["run_time_sec"] += moving_time
            week["run_elev_m"] += elev
        elif group == "bike":
            week["bike_dist_km"] += dist_km
            week["bike_time_sec"] += moving_time
            week["bike_elev_m"] += elev
        elif group == "swim":
            week["swim_dist_m"] += dist_m
            week["swim_time_sec"] += moving_time
        elif group == "strength":
            week["strength_time_sec"] += moving_time

    # Sort activities inside each week chronologically
    for w in weeks.values():
        w["activities"].sort(key=lambda a: a.get("start_date_local", ""))

    return weeks


def _acwr_zone(ratio: float) -> tuple[str, str]:
    """Map an ACWR ratio to its (zone, status) pair."""
    for upper, zone, status in ACWR_ZONES:
        if ratio <= upper:
            return zone, status
    return ACWR_ZONES[-1][1], ACWR_ZONES[-1][2]


def calculate_acwr(
    sorted_week_keys: list[str],
    weeks_dict: dict,
    effort_key: str = "total_relative_effort",
) -> dict[str, dict]:
    """
    Calculate the Acute:Chronic Workload Ratio (ACWR) for each week.

    Acute   = this week's relative effort.
    Chronic = mean weekly effort over the ACWR_CHRONIC_WEEKS weeks *preceding*
              this one. Including the current week in the chronic average pulls
              the mean toward the acute value and compresses every ratio toward
              1.0, which hides exactly the spikes ACWR exists to detect.

    Weeks with fewer than ACWR_MIN_CHRONIC_WEEKS of prior history get
    acwr_ratio=None rather than a ratio computed from too little data.

    `effort_key` selects which week field to score. It defaults to the aggregate,
    but passing e.g. "run_relative_effort" yields per-sport ACWR — the aggregate
    can sit in the optimal zone while one discipline spikes underneath it.
    """
    acwr_by_week = {}

    for i, w_key in enumerate(sorted_week_keys):
        current_effort = weeks_dict[w_key].get(effort_key, 0.0)
        prior_keys = sorted_week_keys[max(0, i - ACWR_CHRONIC_WEEKS) : i]
        prior_efforts = [weeks_dict[k].get(effort_key, 0.0) for k in prior_keys]

        if len(prior_efforts) < ACWR_MIN_CHRONIC_WEEKS:
            acwr_by_week[w_key] = {
                "acute_effort": round(current_effort, 1),
                "chronic_effort": round(sum(prior_efforts) / len(prior_efforts), 1) if prior_efforts else None,
                "acwr_ratio": None,
                "chronic_weeks": len(prior_efforts),
                "status": "Insufficient history",
                "zone": "unknown",
            }
            continue

        chronic_avg = sum(prior_efforts) / len(prior_efforts)
        if chronic_avg <= 0:
            acwr_by_week[w_key] = {
                "acute_effort": round(current_effort, 1),
                "chronic_effort": 0.0,
                "acwr_ratio": None,
                "chronic_weeks": len(prior_efforts),
                "status": "No chronic baseline",
                "zone": "unknown",
            }
            continue

        ratio = round(current_effort / chronic_avg, 2)
        zone, status = _acwr_zone(ratio)
        acwr_by_week[w_key] = {
            "acute_effort": round(current_effort, 1),
            "chronic_effort": round(chronic_avg, 1),
            "acwr_ratio": ratio,
            "chronic_weeks": len(prior_efforts),
            "status": status,
            "zone": zone,
        }

    return acwr_by_week


def build_progression_history(weeks_dict: dict, acwr_map: dict | None = None) -> list[dict]:
    """
    Generate a chronological progression array for multi-week charting.

    Pass `acwr_map` to reuse an ACWR result the caller already computed instead
    of recalculating it over the same weeks.
    """
    sorted_keys = sorted(weeks_dict.keys())
    if acwr_map is None:
        acwr_map = calculate_acwr(sorted_keys, weeks_dict)

    progression = []
    for w_key in sorted_keys:
        w = weeks_dict[w_key]
        m_dt = datetime.fromisoformat(w["week_monday"])
        label = f"{m_dt.day}/{m_dt.month}"

        progression.append({
            "week_key": w_key,
            "label": label,
            "week_monday": w["week_monday"],
            "week_sunday": w["week_sunday"],
            "total_hours": round(w["total_time_sec"] / 3600.0, 2),
            "relative_effort": round(w["total_relative_effort"], 1),
            "elevation_m": round(w["total_elevation_m"]),
            "run_km": round(w["run_dist_km"], 2),
            "bike_km": round(w["bike_dist_km"], 2),
            "swim_km": round(w["swim_dist_m"] / 1000.0, 2),
            "calories": round(w["total_calories"]),
            "acwr": acwr_map.get(w_key, {}),
        })

    return progression


def calculate_hr_zones(hr_max: int = HR_MAX, hr_rest: int = HR_REST) -> dict[str, tuple[int, int]]:
    """
    Calculate 5 Heart Rate Training Zones using Karvonen Heart Rate Reserve (HRR).

    Returns { 'z1': (min_bpm, max_bpm), 'z2': ..., 'z5': ... }
    """
    hrr = max(20, hr_max - hr_rest)
    return {
        "z1": (round(hr_rest + 0.50 * hrr), round(hr_rest + 0.60 * hrr)),
        "z2": (round(hr_rest + 0.60 * hrr) + 1, round(hr_rest + 0.72 * hrr)),
        "z3": (round(hr_rest + 0.72 * hrr) + 1, round(hr_rest + 0.82 * hrr)),
        "z4": (round(hr_rest + 0.82 * hrr) + 1, round(hr_rest + 0.90 * hrr)),
        "z5": (round(hr_rest + 0.90 * hrr) + 1, hr_max),
    }


def estimate_activity_zone_times(act: dict, hr_max: int = HR_MAX, hr_rest: int = HR_REST) -> dict[str, float]:
    """
    Estimate seconds spent in Z1-Z5 for a single workout based on average HR
    and moving time.
    """
    moving_sec = float(act.get("moving_time", 0) or 0)
    if moving_sec <= 0:
        return {"z1": 0.0, "z2": 0.0, "z3": 0.0, "z4": 0.0, "z5": 0.0}

    avg_hr = act.get("average_heartrate")
    if not avg_hr or hr_max <= hr_rest:
        # Without HR data, assume low-intensity Z2 baseline for aerobic endurance
        return {"z1": 0.0, "z2": moving_sec, "z3": 0.0, "z4": 0.0, "z5": 0.0}

    hr_ratio = (avg_hr - hr_rest) / max(20.0, float(hr_max - hr_rest))

    # Spread time across adjacent zones centered around average intensity
    if hr_ratio <= 0.60:
        return {"z1": moving_sec * 0.75, "z2": moving_sec * 0.25, "z3": 0.0, "z4": 0.0, "z5": 0.0}
    elif hr_ratio <= 0.72:
        return {"z1": moving_sec * 0.20, "z2": moving_sec * 0.70, "z3": moving_sec * 0.10, "z4": 0.0, "z5": 0.0}
    elif hr_ratio <= 0.82:
        return {"z1": moving_sec * 0.10, "z2": moving_sec * 0.25, "z3": moving_sec * 0.55, "z4": moving_sec * 0.10, "z5": 0.0}
    elif hr_ratio <= 0.90:
        return {"z1": 0.0, "z2": moving_sec * 0.10, "z3": moving_sec * 0.25, "z4": moving_sec * 0.55, "z5": moving_sec * 0.10}
    else:
        return {"z1": 0.0, "z2": 0.0, "z3": moving_sec * 0.10, "z4": moving_sec * 0.30, "z5": moving_sec * 0.60}


def calculate_polarized_distribution(
    activities: list[dict],
    hr_max: int = HR_MAX,
    hr_rest: int = HR_REST,
) -> dict:
    """
    Compute 3-tier intensity distribution (Low: Z1-Z2, Moderate: Z3, High: Z4-Z5)
    and evaluate adherence to 80/20 Polarized Training principles.
    """
    total_z = {"z1": 0.0, "z2": 0.0, "z3": 0.0, "z4": 0.0, "z5": 0.0}
    by_sport = {g: {"z1": 0.0, "z2": 0.0, "z3": 0.0, "z4": 0.0, "z5": 0.0} for g in SPORT_GROUPS}

    for act in activities:
        group = sport_group(act.get("sport_type") or act.get("type", ""))
        z_times = estimate_activity_zone_times(act, hr_max=hr_max, hr_rest=hr_rest)
        for k, v in z_times.items():
            total_z[k] += v
            if group in by_sport:
                by_sport[group][k] += v

    total_time_sec = sum(total_z.values())
    if total_time_sec <= 0:
        return {
            "total_time_sec": 0,
            "low_intensity_sec": 0,
            "moderate_intensity_sec": 0,
            "high_intensity_sec": 0,
            "low_pct": 0.0,
            "moderate_pct": 0.0,
            "high_pct": 0.0,
            "zones_sec": total_z,
            "classification": "unknown",
            "is_polarized": False,
        }

    low_sec = total_z["z1"] + total_z["z2"]
    mod_sec = total_z["z3"]
    high_sec = total_z["z4"] + total_z["z5"]

    low_pct = round((low_sec / total_time_sec) * 100.0, 1)
    mod_pct = round((mod_sec / total_time_sec) * 100.0, 1)
    high_pct = round((high_sec / total_time_sec) * 100.0, 1)

    # Sports science classification (Seiler Polarized / Pyramidal / Threshold)
    if low_pct >= 75.0 and mod_pct <= 15.0:
        classification = "polarized"  # Gold standard 80/20
    elif low_pct >= 70.0 and mod_pct > high_pct:
        classification = "pyramidal"  # Progressive pyramid
    elif mod_pct >= 25.0:
        classification = "moderate_trap"  # Too much tempo (grey zone)
    elif high_pct >= 25.0:
        classification = "high_intensity_heavy"
    else:
        classification = "balanced"

    return {
        "total_time_sec": round(total_time_sec),
        "low_intensity_sec": round(low_sec),
        "moderate_intensity_sec": round(mod_sec),
        "high_intensity_sec": round(high_sec),
        "low_pct": low_pct,
        "moderate_pct": mod_pct,
        "high_pct": high_pct,
        "zones_sec": {k: round(v) for k, v in total_z.items()},
        "classification": classification,
        "is_polarized": classification == "polarized",
        "by_sport": by_sport,
    }
