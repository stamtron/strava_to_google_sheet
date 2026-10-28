"""
Gear and Shoe Mileage Tracker.

Aggregates cumulative usage and wear metrics for running shoes and bicycles
from Strava activity history, warning the athlete before shoe cushioning degrades
past the injury prevention threshold.
"""

from __future__ import annotations

from typing import Any

from src.config import (
    BIKE_SPORTS,
    GEAR_BIKE_ALERT_KM,
    GEAR_WARNING_FRACTION,
    RUN_SPORTS,
    SHOE_ALERT_KM,
)
from src.formatting import corrected_distance_and_speed

# Strava gear ids encode their kind: "g…" is shoes, "b…" is a bike.
_GEAR_ID_PREFIX_TYPES = {"g": "Shoes", "b": "Bike"}


def _classify_gear(gear_id: str, sport: str) -> str:
    """Gear type from the Strava id prefix, falling back to the activity's sport."""
    prefix_type = _GEAR_ID_PREFIX_TYPES.get(str(gear_id)[:1].lower())
    if prefix_type:
        return prefix_type
    if sport in RUN_SPORTS:
        return "Shoes"
    if sport in BIKE_SPORTS:
        return "Bike"
    return "Gear"


def extract_gear_summary(
    activities: list[dict[str, Any]],
    details: dict[str, dict[str, Any]] | None = None,
    shoe_alert_km: float = SHOE_ALERT_KM,
) -> list[dict[str, Any]]:
    """
    Aggregate activity mileage per gear_id.
    """
    details = details or {}
    gear_stats: dict[str, dict[str, Any]] = {}

    for act in activities:
        act_id = str(act.get("id", ""))
        detail = details.get(act_id) or {}

        gear_id = act.get("gear_id") or detail.get("gear_id")
        if not gear_id:
            continue

        sport = act.get("sport_type") or act.get("type", "Unknown")
        gear_type = _classify_gear(gear_id, sport)

        dist_m, _ = corrected_distance_and_speed(act)
        dist_km = dist_m / 1000.0
        start_date = act.get("start_date_local", "")

        # Try to resolve gear name from detail object if available
        gear_obj = detail.get("gear") or {}
        gear_name = gear_obj.get("name") if isinstance(gear_obj, dict) else None

        if gear_id not in gear_stats or gear_stats[gear_id]["gear_type"] == "Gear":
            # An unrecognised id first seen on e.g. a walk is re-typed once a run
            # or ride reveals what it is; totals carried so far are kept.
            previous = gear_stats.get(gear_id)
            gear_stats[gear_id] = {
                "gear_id": gear_id,
                "name": gear_name,  # None when Strava has no name; presenters fall back to the id
                "gear_type": gear_type,
                "total_km": 0.0,
                "activity_count": 0,
                "first_used": start_date[:10] if start_date else None,
                "last_used": start_date[:10] if start_date else None,
                "threshold_km": float(shoe_alert_km) if gear_type == "Shoes" else GEAR_BIKE_ALERT_KM,
            }
            if previous:
                for key in ("total_km", "activity_count", "first_used", "last_used"):
                    gear_stats[gear_id][key] = previous[key]
                if previous["name"]:
                    gear_stats[gear_id]["name"] = previous["name"]
        elif gear_name and not gear_stats[gear_id]["name"]:
            gear_stats[gear_id]["name"] = gear_name

        stat = gear_stats[gear_id]
        stat["total_km"] += dist_km
        stat["activity_count"] += 1

        dt_str = start_date[:10] if start_date else None
        if dt_str:
            if not stat["first_used"] or dt_str < stat["first_used"]:
                stat["first_used"] = dt_str
            if not stat["last_used"] or dt_str > stat["last_used"]:
                stat["last_used"] = dt_str

    results = []
    for g_id, stat in gear_stats.items():
        total_km = round(stat["total_km"], 1)
        threshold = stat["threshold_km"]
        remaining = max(0.0, round(threshold - total_km, 1))

        if total_km >= threshold:
            status = "retire"
            alert = True
        elif total_km >= (threshold * GEAR_WARNING_FRACTION):
            status = "warning"
            alert = False
        else:
            status = "optimal"
            alert = False

        results.append({
            "gear_id": g_id,
            "name": stat["name"],
            "gear_type": stat["gear_type"],
            "total_km": total_km,
            "activity_count": stat["activity_count"],
            "first_used": stat["first_used"],
            "last_used": stat["last_used"],
            "threshold_km": threshold,
            "remaining_km": remaining,
            "status": status,
            "alert": alert,
        })

    # Sort by total mileage descending
    results.sort(key=lambda x: x["total_km"], reverse=True)
    return results
