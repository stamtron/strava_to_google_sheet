"""Plan-side endpoints: compliance against the plan, gear wear, and Garmin workout push."""

import logging
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.activity_data import load_cache
from src.api.deps import internal_error
from src.config import COMPLIANCE_PLAN_CACHE_TTL_SEC
from src.integrations.plan_source import get_planned_workout_for_date
from src.storage.activity_store import get_activities as store_get_activities
from src.storage.activity_store import get_details, init_db
from src.timeutil import today_local

logger = logging.getLogger(__name__)
router = APIRouter()

_PLANNED_WORKOUT_CACHE: dict[str, tuple[float, dict]] = {}


class WorkoutSyncRequest(BaseModel):
    week_offset: int = 0
    dry_run: bool = False


@router.get("/api/compliance")
def get_workout_compliance(target_date: str = Query(default_factory=lambda: today_local().isoformat())):
    """Evaluate plan vs actual workout execution fidelity for target_date."""
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD") from None

    try:
        return _evaluate_compliance(d, target_date)
    except Exception as e:
        logger.exception("Compliance evaluation failed for %s", target_date)
        raise HTTPException(status_code=500, detail="Compliance evaluation failed.") from e


def _evaluate_compliance(d: date, target_date: str) -> dict:
    now = time.time()
    cached_entry = _PLANNED_WORKOUT_CACHE.get(target_date)
    if cached_entry and (now - cached_entry[0]) < COMPLIANCE_PLAN_CACHE_TTL_SEC:
        planned_info = cached_entry[1]
    else:
        planned_info = get_planned_workout_for_date(d)
        _PLANNED_WORKOUT_CACHE[target_date] = (now, planned_info)

    planned_text = planned_info.get("workout_text", "")

    cache = load_cache()
    activities = cache.get("activities", [])
    if not activities:
        conn = init_db()
        try:
            activities = store_get_activities(conn, limit=120)
        finally:
            conn.close()

    day_activities = [
        a for a in activities
        if (a.get("start_date_local") or "").startswith(target_date)
    ]

    from src.analytics.compliance import evaluate_daily_compliance
    result = evaluate_daily_compliance(planned_text, day_activities)
    result["date"] = target_date
    result["planned_raw"] = planned_text
    return result


@router.get("/api/gear")
def get_gear_tracker():
    """Return equipment and shoe mileage statistics."""
    try:
        return {"gear": _gear_summary()}
    except Exception as e:
        logger.exception("Gear tracker failed")
        raise HTTPException(status_code=500, detail="Gear tracker is unavailable.") from e


def _gear_summary() -> list[dict]:
    cache = load_cache()
    activities = cache.get("activities", [])
    details = cache.get("details", {})
    if not activities:
        conn = init_db()
        try:
            activities = store_get_activities(conn, limit=250)
            details = get_details(conn)
        finally:
            conn.close()

    from src.analytics.gear import extract_gear_summary
    return extract_gear_summary(activities, details=details)


@router.get("/api/workouts/preview")
def preview_workouts_endpoint(week_offset: int = 0):
    """Preview running and cycling workouts parsed from Final Surge for Garmin & Tacx with recovery readiness."""
    from src.integrations.garmin import assess_workout_readiness, get_daily_recovery_metrics
    from src.integrations.garmin_workouts import preview_week_workouts
    try:
        preview = preview_week_workouts(week_offset=week_offset)
        for w in preview.get("workouts", []):
            w_date_str = w.get("date")
            if w_date_str:
                try:
                    w_date = date.fromisoformat(w_date_str)
                    rec = get_daily_recovery_metrics(w_date)
                    w["recovery"] = rec
                    w["readiness"] = assess_workout_readiness(rec, workout_text=w.get("raw_coach_text", ""))
                except Exception as e:
                    # Recovery is decoration on the preview; a Garmin hiccup must not hide the plan.
                    logger.warning("Recovery lookup for %s failed: %s", w_date_str, e)
        return preview
    except Exception as e:
        raise internal_error("Workout preview") from e


@router.post("/api/workouts/sync")
def sync_workouts_endpoint(req: WorkoutSyncRequest = WorkoutSyncRequest()):
    """Sync running and cycling workouts to Garmin Connect Calendar (syncs to Watch & Tacx)."""
    from src.integrations.garmin_workouts import sync_week_workouts_to_garmin
    try:
        return sync_week_workouts_to_garmin(week_offset=req.week_offset, dry_run=req.dry_run)
    except Exception as e:
        raise internal_error("Garmin workout sync") from e
