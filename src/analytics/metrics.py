"""
Training Load, Relative Effort (Suffer Score), and Progression Metrics.
"""

from datetime import datetime, timedelta


def calculate_relative_effort(act: dict) -> float:
    """
    Return Strava suffer score or estimate TRIMP score based on duration and heart rate.
    """
    suffer = act.get("suffer_score")
    if suffer is not None and suffer > 0:
        return float(suffer)

    # Heuristic TRIMP estimate if no raw suffer score
    moving_time_min = (act.get("moving_time", 0) or 0) / 60.0
    avg_hr = act.get("average_heartrate")
    if moving_time_min > 0 and avg_hr:
        # Standard intensity factor based on HR range (assuming max HR ~185, rest HR ~50)
        hr_ratio = max(0.4, min(1.0, (avg_hr - 50) / (185 - 50)))
        # Exponential stress weighting
        stress_factor = hr_ratio * (0.64 * (2.718 ** (1.92 * hr_ratio)))
        return round(moving_time_min * stress_factor, 1)

    # Fallback duration-based load
    return round(moving_time_min * 0.5, 1) if moving_time_min > 0 else 0.0


def process_activities_into_weeks(activities: list[dict]) -> dict:
    """
    Group activities by week (Monday to Sunday) and calculate all sport volumes,
    elevation gains, calories, and Relative Effort (Suffer Score).
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

        weeks[week_key]["activities"].append(act)

        moving_time = act.get("moving_time", 0) or 0
        raw_dist_m = act.get("distance", 0) or 0
        dist_km = raw_dist_m / 1000.0
        elev = float(act.get("total_elevation_gain", 0) or 0)
        sport = act.get("sport_type") or act.get("type", "")
        calories = float(act.get("calories", 0) or (act.get("kilojoules", 0) * 0.239) or 0)
        effort = calculate_relative_effort(act)

        weeks[week_key]["total_time_sec"] += moving_time
        weeks[week_key]["total_elevation_m"] += elev
        weeks[week_key]["total_calories"] += calories
        weeks[week_key]["total_relative_effort"] += effort

        if sport in ["Run", "TrailRun"]:
            weeks[week_key]["run_dist_km"] += dist_km
            weeks[week_key]["run_time_sec"] += moving_time
            weeks[week_key]["run_elev_m"] += elev
        elif sport in ["Ride", "VirtualRide", "GravelRide", "MountainBikeRide"]:
            if act.get("trainer", False) and dist_km < 0.1 and moving_time > 0:
                dist_km = (moving_time / 3600.0) * 21.0
            weeks[week_key]["bike_dist_km"] += dist_km
            weeks[week_key]["bike_time_sec"] += moving_time
            weeks[week_key]["bike_elev_m"] += elev
        elif sport in ["Swim"]:
            dist_m = (dist_km * 1000.0) / 2.0
            weeks[week_key]["swim_dist_m"] += dist_m
            weeks[week_key]["swim_time_sec"] += moving_time
        elif sport in ["WeightTraining", "Workout"]:
            weeks[week_key]["strength_time_sec"] += moving_time

    # Sort activities inside each week chronologically
    for w in weeks.values():
        w["activities"].sort(key=lambda a: a.get("start_date_local", ""))

    return weeks


def calculate_acwr(sorted_week_keys: list[str], weeks_dict: dict) -> dict[str, dict]:
    """
    Calculate Acute:Chronic Workload Ratio (ACWR) for each week.
    Acute = current week effort.
    Chronic = 4-week rolling average effort.
    Optimal ACWR sweet-spot is 0.8 - 1.3 (safe building), > 1.5 indicates overtraining spike.
    """
    acwr_by_week = {}

    for i, w_key in enumerate(sorted_week_keys):
        current_effort = weeks_dict[w_key]["total_relative_effort"]
        # Look back up to 4 weeks (including current)
        window = [weeks_dict[sorted_week_keys[j]]["total_relative_effort"] for j in range(max(0, i - 3), i + 1)]
        chronic_avg = sum(window) / len(window) if window else current_effort
        ratio = round(current_effort / chronic_avg, 2) if chronic_avg > 0 else 1.0

        if ratio < 0.8:
            status = "Χαμηλό Φορτίο (Recovery / Undertraining)"
            badge_color = "#38f9d7"
        elif ratio <= 1.3:
            status = "Βέλτιστη Προσαρμογή (Optimal Zone)"
            badge_color = "#10b981"
        elif ratio <= 1.5:
            status = "Υπερφόρτωση (Overreaching)"
            badge_color = "#f59e0b"
        else:
            status = "Υψηλός Κίνδυνος Κόπωσης (Overtraining Spike)"
            badge_color = "#ff0080"

        acwr_by_week[w_key] = {
            "acute_effort": round(current_effort, 1),
            "chronic_effort": round(chronic_avg, 1),
            "acwr_ratio": ratio,
            "status": status,
            "badge_color": badge_color,
        }

    return acwr_by_week


def build_progression_history(weeks_dict: dict) -> list[dict]:
    """
    Generate chronological progression array for multi-week and monthly charting.
    """
    sorted_keys = sorted(weeks_dict.keys())
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
