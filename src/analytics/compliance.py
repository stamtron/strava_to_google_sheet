"""
Workout Plan vs. Actual Compliance Engine.

Parses coach's prescribed workouts from Google Sheets and compares them against
actual telemetry logged from Strava to calculate volume, pace, and execution fidelity.
"""

from __future__ import annotations

import re
from typing import Any

from src.analytics.metrics import sport_group
from src.config import (
    COMPLIANCE_REST_BROKEN_SHORT_MIN,
    COMPLIANCE_SCORE_COMPLETED,
    COMPLIANCE_SCORE_DEVIATION_FLOOR,
    COMPLIANCE_SCORE_FULL,
    COMPLIANCE_SCORE_OTHER,
    COMPLIANCE_SCORE_REST_BROKEN_LONG,
    COMPLIANCE_SCORE_REST_BROKEN_SHORT,
    COMPLIANCE_SCORE_UNPLANNED,
    COMPLIANCE_SPOT_ON_MAX_PCT,
    COMPLIANCE_SPOT_ON_MIN_PCT,
)
from src.formatting import corrected_distance_and_speed, parse_pace_to_sec

# Section-header keywords, matched on accent-stripped lower-case text. Word
# boundaries keep e.g. "strides" from reading as a ride. Checked in order.
_SPORT_KEYWORDS = (
    ("Swim", re.compile(r"\b(?:κολυμβ\w*|κολυμπ\w*|swim\w*)")),
    ("Ride", re.compile(r"\b(?:ποδηλασ\w*|ποδηλατ\w*|ride|rides|riding|bike|biking|cycling)\b")),
    ("WeightTraining", re.compile(r"\b(?:ενδυναμωσ\w*|βαρη|βαρα|strength|gym)\b")),
    ("Run", re.compile(r"\b(?:τρεξ\w*|run\w*)")),
    ("Rest", re.compile(r"\b(?:ρεπο|rest day|ξεκουρασ\w*)")),
)

# metrics.sport_group key -> the sport label used by parsed plan items.
_GROUP_TO_PLAN_SPORT = {"run": "Run", "bike": "Ride", "swim": "Swim", "strength": "WeightTraining"}


def _normalize_greek(text: str) -> str:
    accents = str.maketrans("άέήίόύώϊϋΐΰΆΈΉΊΌΎΏ", "αεηιουωιυιυΑΕΗΙΟΥΩ")
    return text.translate(accents).lower()


def _clean_sheet_workout_text(text: str) -> str:
    """Strip Greek coaching sheet athlete logging template at the bottom of the cell."""
    if not text:
        return ""
    # Strip everything from the athlete feedback header onwards
    cleaned = re.split(
        r"(?i)\n\s*(?:ΚΟΛΥΜΒΗΣΗ|ΤΡΕΞΙΜΟ|ΠΟΔΗΛΑΣΙΑ|ΕΝΔΥΝΑΜΩΣΗ)?\s*\n?\s*ΣΥΝΟΛΙΚΑ\s+(?:ΜΕΤΡΑ|ΧΙΛΙΟΜΕΤΡΑ|ΧΡΟΝΟΣ)",
        text,
    )[0]
    return cleaned.strip()


def parse_planned_workout(text: str) -> list[dict[str, Any]]:
    """
    Parse planned workout text into structured items.
    Detects sport, target distance (km or m), target duration (mins), and target pace.
    """
    cleaned_text = _clean_sheet_workout_text(text)
    if not cleaned_text:
        return []

    lines = [line.strip() for line in cleaned_text.split("\n") if line.strip()]
    items: list[dict[str, Any]] = []

    current_sport = None
    accumulated_text: list[str] = []

    def flush_item(sport: str | None, text_block: str):
        if not sport and not text_block:
            return
        desc = text_block.strip()

        # Parse distance
        dist_km = None
        km_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:χλμ|km|χιλιομετρα)", desc, re.IGNORECASE)
        if km_match:
            dist_km = float(km_match.group(1).replace(",", "."))
        else:
            m_match = re.search(r"(\d+)\s*(?:μ|m|μετρα)\b", desc, re.IGNORECASE)
            if m_match and int(m_match.group(1)) >= 100:
                dist_km = float(m_match.group(1)) / 1000.0

        # Parse duration (match single quote/min/λεπτά, but avoid double quotes like 30'' which are seconds)
        duration_min = None
        min_match = re.search(r"(\d+)(?:[-–]\d+)?\s*(?:(?<!['’\"])['΄’](?!['’\"])|min|λεπτα)", desc, re.IGNORECASE)
        if min_match:
            duration_min = float(min_match.group(1))

        # Parse target pace
        target_pace_sec = None
        pace_match = re.search(r"(?:@|ρυθμ(?:ο|ός)|pace)\s*(\d{1,2})[:,\.](\d{2})", desc, re.IGNORECASE)
        if not pace_match:
            pace_match = re.search(r"(\d{1,2})[:,\.](\d{2})\s*(?:['΄’]{1,2}|/km|/χλμ)", desc, re.IGNORECASE)
        if pace_match:
            target_pace_sec = int(parse_pace_to_sec(pace_match.group(0)))

        inferred_sport = sport or "Run"
        items.append({
            "sport": inferred_sport,
            "description": desc,
            "target_dist_km": dist_km,
            "target_duration_min": duration_min,
            "target_pace_sec": target_pace_sec,
        })

    for line in lines:
        lower = _normalize_greek(line)
        new_sport = next((sport for sport, pattern in _SPORT_KEYWORDS if pattern.search(lower)), None)

        if new_sport and new_sport != current_sport:
            if current_sport:
                flush_item(current_sport, "\n".join(accumulated_text))
                accumulated_text = []
            current_sport = new_sport

        accumulated_text.append(line)

    if current_sport or accumulated_text:
        flush_item(current_sport, "\n".join(accumulated_text))

    return items


def _map_act_sport_group(act_sport: str) -> str:
    """Normalize activity sport to the plan's sport labels (unknown sports pass through)."""
    return _GROUP_TO_PLAN_SPORT.get(sport_group(act_sport), act_sport)


def evaluate_daily_compliance(
    planned_text: str,
    actual_activities: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compare planned workout against list of actual Strava activities completed on that day.
    """
    planned_items = parse_planned_workout(planned_text)
    is_rest_day = any(p["sport"] == "Rest" for p in planned_items) or (not planned_items and "ρεπο" in (planned_text or "").lower())

    if is_rest_day:
        if not actual_activities:
            return {
                "compliance_score": COMPLIANCE_SCORE_FULL,
                "status": "rest_day_honored",
                "matches": [],
                "unmatched_actual": [],
            }
        # Athlete trained on a rest day
        total_time_min = sum((a.get("moving_time") or 0) / 60.0 for a in actual_activities)
        return {
            "compliance_score": (
                COMPLIANCE_SCORE_REST_BROKEN_SHORT
                if total_time_min < COMPLIANCE_REST_BROKEN_SHORT_MIN
                else COMPLIANCE_SCORE_REST_BROKEN_LONG
            ),
            "status": "rest_day_broken",
            "session_count": len(actual_activities),
            "total_time_min": round(total_time_min, 1),
            "matches": [],
            "unmatched_actual": [a.get("name", "Activity") for a in actual_activities],
        }

    if not planned_items:
        if actual_activities:
            return {
                "compliance_score": COMPLIANCE_SCORE_UNPLANNED,
                "status": "unplanned_training",
                "session_count": len(actual_activities),
                "matches": [],
                "unmatched_actual": [a.get("name", "Activity") for a in actual_activities],
            }
        return {
            "compliance_score": COMPLIANCE_SCORE_FULL,
            "status": "rest_or_empty",
            "matches": [],
            "unmatched_actual": [],
        }

    # Match planned items with actual activities
    matches = []
    used_act_ids = set()

    for item in planned_items:
        p_sport = item["sport"]
        target_km = item["target_dist_km"]
        target_min = item["target_duration_min"]
        target_pace_sec = item["target_pace_sec"]

        best_act = None
        for act in actual_activities:
            act_id = act.get("id")
            if act_id in used_act_ids:
                continue
            act_sport_group = _map_act_sport_group(act.get("sport_type") or act.get("type", ""))
            if act_sport_group == p_sport:
                best_act = act
                used_act_ids.add(act_id)
                break

        if not best_act:
            matches.append({
                "sport": p_sport,
                "status": "missed",
                "planned_km": target_km,
                "actual_km": 0.0,
                "planned_min": target_min,
                "actual_min": 0.0,
                "dur_compliance_pct": 0.0 if target_min else None,
                "planned_pace_sec": target_pace_sec,
                "actual_pace_sec": None,
                "dist_compliance_pct": 0.0 if target_km else None,
            })
            continue

        act_dist_m, _ = corrected_distance_and_speed(best_act)
        act_dist_km = act_dist_m / 1000.0
        moving_time_sec = best_act.get("moving_time") or 0
        # Swim targets (e.g. "@1:45") are per 100 m; everything else is per km.
        is_swim = p_sport == "Swim"
        pace_unit_km = 0.1 if is_swim else 1.0
        actual_pace_sec = (moving_time_sec / (act_dist_km / pace_unit_km)) if act_dist_km > 0 else None

        dist_comp = None
        if target_km and target_km > 0:
            dist_comp = round((act_dist_km / target_km) * 100, 1)

        dur_comp = None
        if target_min and target_min > 0:
            act_min = moving_time_sec / 60.0
            dur_comp = round((act_min / target_min) * 100, 1)

        # Pace difference
        pace_diff = None
        if target_pace_sec and actual_pace_sec:
            pace_diff = round(actual_pace_sec - target_pace_sec, 1)

        # Classification: distance when prescribed, otherwise duration, same bands.
        volume_pct = dist_comp if dist_comp is not None else dur_comp
        if volume_pct is None:
            item_status = "completed"
        elif volume_pct > COMPLIANCE_SPOT_ON_MAX_PCT:
            item_status = "overreach"
        elif volume_pct >= COMPLIANCE_SPOT_ON_MIN_PCT:
            item_status = "spot_on"
        else:
            item_status = "underreach"

        matches.append({
            "sport": p_sport,
            "activity_name": best_act.get("name", ""),
            "status": item_status,
            "planned_km": target_km,
            "actual_km": round(act_dist_km, 2),
            "dist_compliance_pct": dist_comp,
            "planned_min": target_min,
            "actual_min": round(moving_time_sec / 60.0, 1),
            "dur_compliance_pct": dur_comp,
            "planned_pace_sec": target_pace_sec,
            "actual_pace_sec": round(actual_pace_sec, 1) if actual_pace_sec else None,
            "pace_diff_sec": pace_diff,
            "pace_unit": "per_100m" if is_swim else "per_km",
            "moving_time_min": round(moving_time_sec / 60.0, 1),
        })

    # Unmatched activities
    unmatched = [
        act.get("name", "Activity")
        for act in actual_activities
        if act.get("id") not in used_act_ids
    ]

    # Calculate overall compliance score
    if not matches:
        score = 0
    else:
        scores = []
        for m in matches:
            st = m["status"]
            if st == "spot_on":
                scores.append(COMPLIANCE_SCORE_FULL)
            elif st == "completed":
                scores.append(COMPLIANCE_SCORE_COMPLETED)
            elif st in ("overreach", "underreach"):
                # Over/underreach always carry a volume percentage (see classification).
                pct = m.get("dist_compliance_pct") if m.get("dist_compliance_pct") is not None else m.get("dur_compliance_pct")
                deviation = abs(100 - pct)
                scores.append(max(COMPLIANCE_SCORE_DEVIATION_FLOOR, int(COMPLIANCE_SCORE_FULL - deviation)))
            elif st == "missed":
                scores.append(0)
            else:
                scores.append(COMPLIANCE_SCORE_OTHER)
        score = int(sum(scores) / len(scores))

    return {
        "compliance_score": score,
        "status": "evaluated",
        "matches": matches,
        "unmatched_actual": unmatched,
    }
