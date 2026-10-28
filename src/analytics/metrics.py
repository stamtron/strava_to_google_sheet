"""
Training Load, Relative Effort (Suffer Score), and Progression Metrics.
"""

import math
from datetime import date, datetime, timedelta
from itertools import pairwise

from src.config import (
    ACWR_CHRONIC_WEEKS,
    ACWR_LOW_MAX,
    ACWR_MIN_CHRONIC_WEEKS,
    ACWR_OPTIMAL_MAX,
    ACWR_OVERREACHING_MAX,
    ATL_DAYS,
    BIKE_SPORTS,
    CTL_DAYS,
    FITNESS_DISPLAY_DAYS,
    GREY_ZONE_MODERATE_MIN_PCT,
    HIGH_INTENSITY_HEAVY_MIN_PCT,
    HR_MAX,
    HR_REST,
    HR_ZONE_HRR_EDGES,
    HR_ZONE_TIME_SPREAD,
    KJ_TO_KCAL_FACTOR,
    MIN_HRR_BPM,
    POLARIZED_LOW_MIN_PCT,
    POLARIZED_MODERATE_MAX_PCT,
    PYRAMIDAL_LOW_MIN_PCT,
    RPE_HRR_ANCHORS,
    RPE_SUFFER_PER_MIN_ANCHORS,
    RUN_SPORTS,
    STRENGTH_SPORTS,
    SWIM_SPORTS,
    TRIMP_DURATION_ONLY_PER_MIN,
    TRIMP_EXPONENT,
    TRIMP_HRR_FLOOR,
    TRIMP_WEIGHT,
    TSB_FRESH_MAX,
    TSB_FRESH_MIN,
    TSB_OPTIMAL_MAX,
    TSB_OPTIMAL_MIN,
)
from src.formatting import corrected_distance_and_speed
from src.timeutil import today_local

# ACWR interpretation bands as (inclusive upper edge, zone key). Keys are
# machine-readable; presentation layers map them to labels and colours.
ACWR_ZONES = (
    (ACWR_LOW_MAX, "low"),
    (ACWR_OPTIMAL_MAX, "optimal"),
    (ACWR_OVERREACHING_MAX, "overreaching"),
    (float("inf"), "spike"),
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
        hr_ratio = max(TRIMP_HRR_FLOOR, min(1.0, (avg_hr - HR_REST) / (HR_MAX - HR_REST)))
        stress_factor = hr_ratio * TRIMP_WEIGHT * math.exp(TRIMP_EXPONENT * hr_ratio)
        return round(moving_time_min * stress_factor, 1)

    # Fallback duration-based load
    return round(moving_time_min * TRIMP_DURATION_ONLY_PER_MIN, 1) if moving_time_min > 0 else 0.0


def _interpolate_rpe(value: float, anchors: tuple[tuple[float, float], ...]) -> int:
    """Piecewise-linear lookup of `value` on (input, rpe) anchors, clamped to 1-10."""
    if value <= anchors[0][0]:
        raw_rpe = anchors[0][1]
    elif value >= anchors[-1][0]:
        raw_rpe = anchors[-1][1]
    else:
        raw_rpe = anchors[-1][1]
        for (x0, y0), (x1, y1) in pairwise(anchors):
            if value < x1:
                raw_rpe = y0 + (value - x0) / (x1 - x0) * (y1 - y0)
                break
    return max(1, min(10, round(raw_rpe)))


def normalize_rpe(
    act: dict,
    detail: dict | None = None,
    hr_max: int = HR_MAX,
    hr_rest: int = HR_REST,
) -> int:
    """
    Calculate or normalize Rate of Perceived Exertion (RPE) on a 1-10 Borg CR10 scale.

    Hierarchy:
    1. Strava's explicit `perceived_exertion` (if athlete entered it on Strava, 1-10).
    2. Karvonen Heart Rate Reserve (%HRR) mapping if average_heartrate is available.
    3. Strava suffer_score / moving_time intensity ratio if heart rate is unavailable.
    4. Default moderate baseline (4) if no metrics exist.
    """
    # 1. Explicit perceived exertion from Strava (detail or summary)
    detail_pe = detail.get("perceived_exertion") if detail else None
    act_pe = act.get("perceived_exertion")
    explicit_pe = detail_pe if detail_pe is not None else act_pe
    if explicit_pe is not None:
        try:
            val = float(explicit_pe)
            if val > 0:
                return max(1, min(10, round(val)))
        except (ValueError, TypeError):
            pass

    # 2. Average Heart Rate via Karvonen %HRR
    avg_hr = act.get("average_heartrate")
    if avg_hr and hr_max > hr_rest:
        hrr_pct = (avg_hr - hr_rest) / max(float(MIN_HRR_BPM), float(hr_max - hr_rest))
        return _interpolate_rpe(hrr_pct, RPE_HRR_ANCHORS)

    # 3. Strava Suffer Score per minute intensity
    suffer = (detail.get("suffer_score") if detail else None) or act.get("suffer_score")
    moving_sec = act.get("moving_time", 0) or 0
    if suffer and moving_sec > 0:
        intensity = float(suffer) / (moving_sec / 60.0)
        return _interpolate_rpe(intensity, RPE_SUFFER_PER_MIN_ANCHORS)

    return 4


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
        try:
            act_date = datetime.fromisoformat(raw_dt).date()
        except (ValueError, TypeError, AttributeError):
            continue
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
        calories = float(act.get("calories") or 0) or float(act.get("kilojoules") or 0) * KJ_TO_KCAL_FACTOR

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


def _acwr_zone(ratio: float) -> str:
    """Map an ACWR ratio to its zone key."""
    for upper, zone in ACWR_ZONES:
        if ratio <= upper:
            return zone
    return ACWR_ZONES[-1][1]


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

    Preceding weeks are found by calendar offset (monday - 7·k), so a week with
    no activities counts as zero load instead of being skipped — skipping it
    would leave a deload or injury break out of the baseline and hide the spike
    on return. History is measured in calendar weeks since the earliest week in
    `weeks_dict`; weeks with fewer than ACWR_MIN_CHRONIC_WEEKS of it get
    acwr_ratio=None rather than a ratio computed from too little data.

    `effort_key` selects which week field to score. It defaults to the aggregate,
    but passing e.g. "run_relative_effort" yields per-sport ACWR — the aggregate
    can sit in the optimal zone while one discipline spikes underneath it.
    """
    acwr_by_week = {}
    if not sorted_week_keys:
        return acwr_by_week

    first_monday = min(date.fromisoformat(k) for k in (weeks_dict.keys() or sorted_week_keys))

    for w_key in sorted_week_keys:
        current_effort = weeks_dict[w_key].get(effort_key, 0.0)
        monday = date.fromisoformat(w_key)
        history_weeks = max(0, (monday - first_monday).days // 7)
        lookback = min(ACWR_CHRONIC_WEEKS, history_weeks)
        preceding_weeks = [(monday - timedelta(weeks=k)).isoformat() for k in range(lookback, 0, -1)]
        chronic_weeks = preceding_weeks[-ACWR_CHRONIC_WEEKS:]
        prior_efforts = [weeks_dict.get(k, {}).get(effort_key, 0.0) for k in chronic_weeks]

        if len(prior_efforts) < ACWR_MIN_CHRONIC_WEEKS:
            acwr_by_week[w_key] = {
                "acute_effort": round(current_effort, 1),
                "chronic_effort": round(sum(prior_efforts) / len(prior_efforts), 1) if prior_efforts else None,
                "acwr_ratio": None,
                "chronic_weeks": len(prior_efforts),
                "status": "insufficient_history",
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
                "status": "no_baseline",
                "zone": "unknown",
            }
            continue

        ratio = round(current_effort / chronic_avg, 2)
        zone = _acwr_zone(ratio)
        acwr_by_week[w_key] = {
            "acute_effort": round(current_effort, 1),
            "chronic_effort": round(chronic_avg, 1),
            "acwr_ratio": ratio,
            "chronic_weeks": len(prior_efforts),
            "status": zone,
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
        s_dt = datetime.fromisoformat(w["week_sunday"])
        label = f"{s_dt.day}/{s_dt.month}"

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


_ZONE_KEYS = ("z1", "z2", "z3", "z4", "z5")


def calculate_hr_zones(hr_max: int = HR_MAX, hr_rest: int = HR_REST) -> dict[str, tuple[int, int]]:
    """
    Calculate 5 Heart Rate Training Zones using Karvonen Heart Rate Reserve (HRR).

    Returns { 'z1': (min_bpm, max_bpm), 'z2': ..., 'z5': ... }
    """
    hrr = max(MIN_HRR_BPM, hr_max - hr_rest)
    bpm = [round(hr_rest + edge * hrr) for edge in HR_ZONE_HRR_EDGES]
    zones = {}
    for i, key in enumerate(_ZONE_KEYS):
        low = bpm[i] if i == 0 else bpm[i] + 1
        high = bpm[i + 1] if i + 1 < len(bpm) else hr_max
        zones[key] = (low, high)
    return zones


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

    hr_ratio = (avg_hr - hr_rest) / max(float(MIN_HRR_BPM), float(hr_max - hr_rest))

    # Spread time across adjacent zones centered around average intensity. The
    # zone of the average is the number of zone edges (above Z1's) it exceeds.
    zone_idx = sum(1 for edge in HR_ZONE_HRR_EDGES[1:] if hr_ratio > edge)
    spread = HR_ZONE_TIME_SPREAD[zone_idx]
    return {key: moving_sec * share for key, share in zip(_ZONE_KEYS, spread)}


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
            "by_sport": by_sport,
        }

    low_sec = total_z["z1"] + total_z["z2"]
    mod_sec = total_z["z3"]
    high_sec = total_z["z4"] + total_z["z5"]

    low_pct = round((low_sec / total_time_sec) * 100.0, 1)
    mod_pct = round((mod_sec / total_time_sec) * 100.0, 1)
    high_pct = round((high_sec / total_time_sec) * 100.0, 1)

    # Sports science classification (Seiler Polarized / Pyramidal / Threshold)
    if low_pct >= POLARIZED_LOW_MIN_PCT and mod_pct <= POLARIZED_MODERATE_MAX_PCT:
        classification = "polarized"  # Gold standard 80/20
    elif low_pct >= PYRAMIDAL_LOW_MIN_PCT and mod_pct > high_pct:
        classification = "pyramidal"  # Progressive pyramid
    elif mod_pct >= GREY_ZONE_MODERATE_MIN_PCT:
        classification = "moderate_trap"  # Too much tempo (grey zone)
    elif high_pct >= HIGH_INTENSITY_HEAVY_MIN_PCT:
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


def _classify_tsb(tsb: float) -> str:
    """Classify Training Stress Balance into a status key."""
    if tsb > TSB_FRESH_MAX:
        return "transition"
    elif tsb >= TSB_FRESH_MIN:
        return "fresh"
    elif tsb >= TSB_OPTIMAL_MAX:
        return "neutral"
    elif tsb >= TSB_OPTIMAL_MIN:
        return "optimal"
    else:
        return "high_fatigue"


def calculate_fitness_fatigue(
    activities: list[dict],
    ctl_days: int = CTL_DAYS,
    atl_days: int = ATL_DAYS,
    display_days: int = FITNESS_DISPLAY_DAYS,
) -> dict:
    """
    Calculate daily Chronic Training Load (CTL / Fitness), Acute Training Load
    (ATL / Fatigue), and Training Stress Balance (TSB / Form = CTL - ATL)
    using the Banister Impulse-Response Exponentially Weighted Moving Average (EWMA) model.

    To ensure mathematical accuracy and eliminate cold-start bias, the EWMA is warmed up
    from the earliest recorded activity in the provided set, and then sliced to
    the most recent `display_days` up through today. Activities dated after
    today (athlete-local) are ignored so `current` is always today's state.

    Returns:
    {
        "current": {
            "date": str,
            "ctl": float,
            "atl": float,
            "tsb": float,
            "status": str,  # transition | fresh | neutral | optimal | high_fatigue
            "ctl_ramp_7d": float,
        },
        "series": [
            {
                "date": str,
                "day_load": float,
                "ctl": float,
                "atl": float,
                "tsb": float,
                "status": str,
            },
            ...
        ]
    }
    """
    if ctl_days <= 0 or atl_days <= 0:
        raise ValueError("Time constants ctl_days and atl_days must be positive.")

    # 1. Bucket daily loads
    daily_loads: dict[date, float] = {}
    for act in activities:
        raw_dt = act.get("start_date_local", "")
        if not raw_dt:
            continue
        try:
            act_date = datetime.fromisoformat(raw_dt).date()
            effort = calculate_relative_effort(act)
            daily_loads[act_date] = daily_loads.get(act_date, 0.0) + effort
        except (ValueError, TypeError):
            continue

    today = today_local()
    if daily_loads:
        start_date = min(daily_loads.keys())
        # Warm up at least 2 × ctl_days before the display window to stabilize CTL
        display_start = today - timedelta(days=display_days)
        start_date = min(start_date, display_start - timedelta(days=ctl_days * 2))
    else:
        start_date = today - timedelta(days=display_days)

    end_date = today

    # 2. Iterate daily and update EWMA
    ctl_decay = math.exp(-1.0 / float(ctl_days))
    atl_decay = math.exp(-1.0 / float(atl_days))

    ctl = 0.0
    atl = 0.0

    full_series = []
    curr = start_date

    while curr <= end_date:
        load = daily_loads.get(curr, 0.0)
        ctl = (ctl * ctl_decay) + (load * (1.0 - ctl_decay))
        atl = (atl * atl_decay) + (load * (1.0 - atl_decay))
        tsb = ctl - atl
        status_key = _classify_tsb(tsb)

        full_series.append({
            "date": curr.isoformat(),
            "day_load": round(load, 1),
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(tsb, 1),
            "status": status_key,
        })
        curr += timedelta(days=1)

    display_start_iso = (today - timedelta(days=display_days)).isoformat()
    visible_series = [s for s in full_series if s["date"] >= display_start_iso]

    # Current snapshot
    current_entry = full_series[-1] if full_series else {
        "date": today.isoformat(),
        "day_load": 0.0,
        "ctl": 0.0,
        "atl": 0.0,
        "tsb": 0.0,
        "status": "neutral",
    }

    # 7-day ramp rate for CTL (fitness progression slope)
    ctl_ramp_7d = 0.0
    if len(full_series) >= 8:
        ctl_ramp_7d = round(full_series[-1]["ctl"] - full_series[-8]["ctl"], 1)

    current_summary = {
        "date": current_entry["date"],
        "ctl": current_entry["ctl"],
        "atl": current_entry["atl"],
        "tsb": current_entry["tsb"],
        "status": current_entry["status"],
        "ctl_ramp_7d": ctl_ramp_7d,
    }

    return {
        "current": current_summary,
        "series": visible_series,
    }
