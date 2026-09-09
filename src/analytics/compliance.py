"""
Workout Plan vs. Actual Compliance Engine.

Parses coach's prescribed workouts from Google Sheets and compares them against
actual telemetry logged from Strava to calculate volume, pace, and execution fidelity.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from src.config import BIKE_SPORTS, RUN_SPORTS, STRENGTH_SPORTS, SWIM_SPORTS
from src.formatting import corrected_distance_and_speed, format_pace


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
            mins = int(pace_match.group(1))
            secs = int(pace_match.group(2))
            target_pace_sec = mins * 60 + secs

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
        new_sport = None
        if any(w in lower for w in ("κολυμβηση", "κολυμπι", "swim")):
            new_sport = "Swim"
        elif any(w in lower for w in ("ποδηλασια", "ποδηλατο", "ride", "bike", "cycling")):
            new_sport = "Ride"
        elif any(w in lower for w in ("ενδυναμωση", "βαρη", "strength", "gym")):
            new_sport = "WeightTraining"
        elif any(w in lower for w in ("τρεξιμο", "run")):
            new_sport = "Run"
        elif any(w in lower for w in ("ρεπο", "rest day", "ξεκουραση")):
            new_sport = "Rest"

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
    """Normalize activity sport to canonical groups."""
    if act_sport in RUN_SPORTS:
        return "Run"
    if act_sport in BIKE_SPORTS:
        return "Ride"
    if act_sport in SWIM_SPORTS:
        return "Swim"
    if act_sport in STRENGTH_SPORTS:
        return "WeightTraining"
    return act_sport


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
                "compliance_score": 100,
                "status": "rest_day_honored",
                "summary": "Rest day honored. Zero training load recorded.",
                "matches": [],
                "unmatched_actual": [],
            }
        # Athlete trained on a rest day
        total_time_min = sum((a.get("moving_time") or 0) / 60.0 for a in actual_activities)
        return {
            "compliance_score": 50 if total_time_min < 30 else 30,
            "status": "rest_day_broken",
            "summary": f"Rest day scheduled, but {len(actual_activities)} session(s) totaling {total_time_min:.0f}m logged.",
            "matches": [],
            "unmatched_actual": [a.get("name", "Activity") for a in actual_activities],
        }

    if not planned_items:
        if actual_activities:
            return {
                "compliance_score": 80,
                "status": "unplanned_training",
                "summary": f"{len(actual_activities)} activity(ies) logged without a scheduled plan.",
                "matches": [],
                "unmatched_actual": [a.get("name", "Activity") for a in actual_activities],
            }
        return {
            "compliance_score": 100,
            "status": "rest_or_empty",
            "summary": "No workout planned and no activities recorded.",
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
                "notes": f"Prescribed {p_sport} session was missed.",
            })
            continue

        act_dist_m, _ = corrected_distance_and_speed(best_act)
        act_dist_km = act_dist_m / 1000.0
        moving_time_sec = best_act.get("moving_time") or 0
        actual_pace_sec = (moving_time_sec / act_dist_km) if act_dist_km > 0 else None

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

        # Classification
        if dist_comp is not None:
            if 88.0 <= dist_comp <= 112.0:
                item_status = "spot_on"
            elif dist_comp > 115.0:
                item_status = "overreach"
            else:
                item_status = "underreach"
        elif target_min:
            act_min = moving_time_sec / 60.0
            dur_pct = (act_min / target_min) * 100
            if 85.0 <= dur_pct <= 115.0:
                item_status = "spot_on"
            elif dur_pct > 115.0:
                item_status = "overreach"
            else:
                item_status = "underreach"
        else:
            item_status = "completed"

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
                scores.append(100)
            elif st == "completed":
                scores.append(90)
            elif st in ("overreach", "underreach"):
                pct = m.get("dist_compliance_pct") if m.get("dist_compliance_pct") is not None else m.get("dur_compliance_pct")
                if pct is None:
                    pct = 80.0
                scores.append(max(40, int(100 - abs(100 - pct))))
            elif st == "missed":
                scores.append(0)
            else:
                scores.append(70)
        score = int(sum(scores) / len(scores))

    summary_parts = []
    for m in matches:
        comp_pct = m.get("dist_compliance_pct") if m.get("dist_compliance_pct") is not None else m.get("dur_compliance_pct")
        if m["status"] == "spot_on":
            if m.get("planned_km") and m.get("actual_km") is not None:
                summary_parts.append(f"{m['sport']}: Executed accurately ({m['actual_km']}km vs planned {m['planned_km']}km).")
            elif m.get("planned_min") and m.get("actual_min") is not None:
                summary_parts.append(f"{m['sport']}: Executed accurately ({m['actual_min']:.0f}m vs planned {m['planned_min']:.0f}m).")
            else:
                summary_parts.append(f"{m['sport']}: Executed accurately.")
        elif m["status"] == "overreach":
            pct_str = f" (+{comp_pct - 100:.0f}%)" if comp_pct is not None else ""
            summary_parts.append(f"{m['sport']}: Volume overshot{pct_str}. Watch recovery.")
        elif m["status"] == "underreach":
            if m.get("planned_km") and m.get("actual_km") is not None:
                summary_parts.append(f"{m['sport']}: Volume under prescribed target ({m['actual_km']}km of {m['planned_km']}km).")
            elif m.get("planned_min") and m.get("actual_min") is not None:
                summary_parts.append(f"{m['sport']}: Volume under prescribed target ({m['actual_min']:.0f}m of {m['planned_min']:.0f}m).")
            elif comp_pct is not None:
                summary_parts.append(f"{m['sport']}: Volume under prescribed target ({comp_pct:.0f}%).")
            else:
                summary_parts.append(f"{m['sport']}: Volume under prescribed target.")
        elif m["status"] == "missed":
            summary_parts.append(f"{m['sport']}: Session was not completed.")

    return {
        "compliance_score": score,
        "status": "evaluated",
        "summary": " ".join(summary_parts) or "Workout evaluated.",
        "matches": matches,
        "unmatched_actual": unmatched,
    }
