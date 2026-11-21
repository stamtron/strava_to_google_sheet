"""
Workout Plan vs. Actual Compliance Engine.

Parses coach's prescribed workouts (Final Surge plan text) and compares them against
actual telemetry logged from Strava to calculate volume, pace, and execution fidelity.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
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
from src.formatting import distance_and_speed, parse_pace_to_sec

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

    # Match planned items with actual activities. Same-sport activities pair with
    # same-sport plan items in chronological order; any extra activities fold into
    # the last plan item of that sport, so a run split into several recordings
    # (warm-up, main set, cool-down) is scored as the one session it was.
    acts_by_sport: dict[str, list[dict[str, Any]]] = {}
    for act in sorted(actual_activities, key=lambda a: a.get("start_date_local") or ""):
        sport = _map_act_sport_group(act.get("sport_type") or act.get("type", ""))
        acts_by_sport.setdefault(sport, []).append(act)

    items_by_sport: dict[str, list[int]] = {}
    for idx, item in enumerate(planned_items):
        items_by_sport.setdefault(item["sport"], []).append(idx)

    assigned: dict[int, list[dict[str, Any]]] = {}
    for sport, item_idxs in items_by_sport.items():
        acts = acts_by_sport.get(sport, [])
        for pos, idx in enumerate(item_idxs):
            is_last = pos == len(item_idxs) - 1
            assigned[idx] = acts[pos:] if is_last else acts[pos:pos + 1]

    matches = []
    used_act_ids = {a.get("id") for acts in assigned.values() for a in acts}

    for idx, item in enumerate(planned_items):
        p_sport = item["sport"]
        target_km = item["target_dist_km"]
        target_min = item["target_duration_min"]
        target_pace_sec = item["target_pace_sec"]
        acts = assigned.get(idx, [])

        if not acts:
            matches.append({
                "sport": p_sport,
                "status": "missed",
                "activity_count": 0,
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

        act_dist_km = sum(distance_and_speed(a)[0] for a in acts) / 1000.0
        moving_time_sec = sum(a.get("moving_time") or 0 for a in acts)
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
            "activity_name": ", ".join(a.get("name", "") for a in acts),
            "activity_count": len(acts),
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
            if st in ("spot_on", "overreach", "underreach"):
                # Score the actual deviation, even inside the spot-on band:
                # 8 of 9 km is close, not 100%. The band only sets the status.
                pct = m.get("dist_compliance_pct") if m.get("dist_compliance_pct") is not None else m.get("dur_compliance_pct")
                deviation = abs(100 - pct)
                scores.append(max(COMPLIANCE_SCORE_DEVIATION_FLOOR, round(COMPLIANCE_SCORE_FULL - deviation)))
            elif st == "completed":
                scores.append(COMPLIANCE_SCORE_COMPLETED)
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


# Final Surge's session total line, e.g. "Planned: 9 km".
PLANNED_TOTAL_RE = re.compile(r"planned:\s*\d", re.IGNORECASE)


def evaluate_weekly_compliance(days: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """
    Aggregate a Monday–Sunday week of plan vs actual.

    `days` holds one entry per calendar day: {date, planned_text, activities}.
    Days after `today` are "future" (plan shown, not scored). Today counts as
    "pending" while a planned session simply hasn't been done yet, so the week
    isn't marked down at breakfast. The weekly score is the mean daily score over
    scored days that had a plan or training; empty unplanned days don't count.

    Running km are compared plan vs actual (interval rep sets excluded from the
    planned side, see below): planned over the scored days (so an
    unfinished week compares like with like) and over the whole week; actual
    counts every run, planned or not.
    """
    from src.analytics.session_type import REPS_RE, classify_planned_session

    out_days = []
    scores = []
    planned_run_km_total = planned_run_km_to_date = actual_run_km = 0.0
    sessions_planned = sessions_done = key_planned = key_done = 0

    for day in sorted(days, key=lambda d: d["date"]):
        d: date = day["date"]
        text = day.get("planned_text") or ""
        acts = day.get("activities") or []
        result = evaluate_daily_compliance(text, acts)
        session = classify_planned_session(text) if text.strip() else {"is_key": False, "intensity": "rest"}
        matches = result.get("matches", [])

        # A bare rep set ("6x800m") parses to one rep's distance and omits warm-up/
        # cool-down, so it stays out of the km comparison unless Final Surge's
        # "Planned: 9 km" session total is present.
        day_planned_run_km = sum(
            item["target_dist_km"] or 0 for item in parse_planned_workout(text)
            if item["sport"] == "Run"
            and (PLANNED_TOTAL_RE.search(item["description"]) or not REPS_RE.search(item["description"]))
        )
        day_actual_run_km = sum(
            distance_and_speed(a)[0] / 1000.0 for a in acts
            if sport_group(a.get("sport_type") or a.get("type", "")) == "run"
        )
        planned_run_km_total += day_planned_run_km

        if d > today:
            state = "future"
        elif d == today and not acts and any(m.get("status") == "missed" for m in matches):
            state = "pending"
        else:
            state = "scored"

        executed = [m for m in matches if m.get("status") != "missed"]
        if state == "scored":
            planned_run_km_to_date += day_planned_run_km
            actual_run_km += day_actual_run_km
            if result.get("status") != "rest_or_empty":
                scores.append(result["compliance_score"])
            sessions_planned += len(matches)
            sessions_done += len(executed)
            if session["is_key"] and matches:
                key_planned += 1
                key_done += 1 if executed else 0

        out_days.append({
            "date": d.isoformat(),
            "state": state,
            "status": result.get("status"),
            "compliance_score": result["compliance_score"] if state == "scored" else None,
            "planned_run_km": round(day_planned_run_km, 2),
            "actual_run_km": round(day_actual_run_km, 2),
            "is_key": session["is_key"],
            "intensity": session["intensity"],
            "matches": matches,
            "unmatched_actual": result.get("unmatched_actual", []),
            "planned_raw": text,
        })

    has_plan = any((day.get("planned_text") or "").strip() for day in days)
    monday = min(d["date"] for d in days) if days else today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return {
        "week_monday": monday.isoformat(),
        "week_sunday": sunday.isoformat(),
        "in_progress": monday <= today <= sunday,
        # Without any plan on record there is nothing to comply with: unplanned
        # training alone would otherwise read as a flat score.
        "has_plan": has_plan,
        "weekly_score": round(sum(scores) / len(scores)) if scores and has_plan else None,
        "scored_days": len(scores),
        "planned_run_km": round(planned_run_km_total, 1),
        "planned_run_km_to_date": round(planned_run_km_to_date, 1),
        "actual_run_km": round(actual_run_km, 1),
        "sessions_planned": sessions_planned,
        "sessions_done": sessions_done,
        "key_sessions_planned": key_planned,
        "key_sessions_done": key_done,
        "days": out_days,
    }


def week_day_inputs(
    week_monday: date,
    plan_texts: dict[date, str],
    activities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Slice plan texts and activities into the 7 day entries evaluate_weekly_compliance takes."""
    by_day: dict[str, list[dict[str, Any]]] = {}
    for act in activities:
        by_day.setdefault((act.get("start_date_local") or "")[:10], []).append(act)
    days = []
    for i in range(7):
        d = week_monday + timedelta(days=i)
        days.append({"date": d, "planned_text": plan_texts.get(d, ""), "activities": by_day.get(d.isoformat(), [])})
    return days
