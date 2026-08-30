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
    (0.8, "low", "Χαμηλό Φορτίο (Recovery / Undertraining)"),
    (1.3, "optimal", "Βέλτιστη Προσαρμογή (Optimal Zone)"),
    (1.5, "overreaching", "Υπερφόρτωση (Overreaching)"),
    (float("inf"), "spike", "Υψηλός Κίνδυνος Κόπωσης (Overtraining Spike)"),
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
            }

        week = weeks[week_key]
        week["activities"].append(act)

        moving_time = act.get("moving_time", 0) or 0
        dist_m, _ = corrected_distance_and_speed(act)
        dist_km = dist_m / 1000.0
        elev = float(act.get("total_elevation_gain", 0) or 0)
        sport = act.get("sport_type") or act.get("type", "")
        calories = float(act.get("calories", 0) or (act.get("kilojoules", 0) * 0.239) or 0)

        week["total_time_sec"] += moving_time
        week["total_elevation_m"] += elev
        week["total_calories"] += calories
        week["total_relative_effort"] += calculate_relative_effort(act)

        if sport in RUN_SPORTS:
            week["run_dist_km"] += dist_km
            week["run_time_sec"] += moving_time
            week["run_elev_m"] += elev
        elif sport in BIKE_SPORTS:
            week["bike_dist_km"] += dist_km
            week["bike_time_sec"] += moving_time
            week["bike_elev_m"] += elev
        elif sport in SWIM_SPORTS:
            week["swim_dist_m"] += dist_m
            week["swim_time_sec"] += moving_time
        elif sport in STRENGTH_SPORTS:
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


def calculate_acwr(sorted_week_keys: list[str], weeks_dict: dict) -> dict[str, dict]:
    """
    Calculate the Acute:Chronic Workload Ratio (ACWR) for each week.

    Acute   = this week's relative effort.
    Chronic = mean weekly effort over the ACWR_CHRONIC_WEEKS weeks *preceding*
              this one. Including the current week in the chronic average pulls
              the mean toward the acute value and compresses every ratio toward
              1.0, which hides exactly the spikes ACWR exists to detect.

    Weeks with fewer than ACWR_MIN_CHRONIC_WEEKS of prior history get
    acwr_ratio=None rather than a ratio computed from too little data.
    """
    acwr_by_week = {}

    for i, w_key in enumerate(sorted_week_keys):
        current_effort = weeks_dict[w_key]["total_relative_effort"]
        prior_keys = sorted_week_keys[max(0, i - ACWR_CHRONIC_WEEKS) : i]
        prior_efforts = [weeks_dict[k]["total_relative_effort"] for k in prior_keys]

        if len(prior_efforts) < ACWR_MIN_CHRONIC_WEEKS:
            acwr_by_week[w_key] = {
                "acute_effort": round(current_effort, 1),
                "chronic_effort": round(sum(prior_efforts) / len(prior_efforts), 1) if prior_efforts else None,
                "acwr_ratio": None,
                "chronic_weeks": len(prior_efforts),
                "status": "Ανεπαρκές ιστορικό (Insufficient history)",
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
                "status": "Χωρίς χρόνιο φορτίο (No chronic baseline)",
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
