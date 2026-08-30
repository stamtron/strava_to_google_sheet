"""
FastAPI Server for Strava & Garmin Training Dashboard.

Provides REST endpoints for activities, Garmin biometrics,
Relative Effort & ACWR tracking, multi-week progression analytics,
AI coaching, and Google Sheets synchronization.
"""

import json
import os
import time
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import (
    ACTIVITIES_CACHE_FILE,
    ACTIVITIES_CACHE_TTL,
    ALLOWED_ORIGINS,
    STRAVA_DETAIL_DELAY_SEC,
    WEB_DIR,
)
from src.integrations.strava import (
    StravaAuthRequired,
    StravaNetworkError,
    StravaRateLimitError,
    fetch_activities,
    fetch_details_for_activities,
    get_access_token,
)
from src.integrations.garmin import get_weekly_health_summaries
from src.integrations.sheets import write_to_sheet
from src.analytics.metrics import (
    build_progression_history,
    calculate_acwr,
    process_activities_into_weeks,
)
from src.analytics.ai_coach import (
    generate_weekly_coaching_insights,
    predict_race_performances,
    predict_triathlon_performances,
)

app = FastAPI(title="Endurance AI Training API", version="2.0.0")

# Explicit origins, credentials off: the API is unauthenticated, so a wildcard
# origin with credentials would let any page the browser visits read this data.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class FeedbackRequest(BaseModel):
    week_summary: dict
    garmin_health: dict | None = None
    athlete_notes: str = ""


class SyncRequest(BaseModel):
    count: int = 35


def _load_cache() -> dict:
    """Return the activities cache, or an empty skeleton if unreadable."""
    if os.path.exists(ACTIVITIES_CACHE_FILE):
        try:
            with open(ACTIVITIES_CACHE_FILE, "r") as f:
                data = json.load(f)
            return {
                "activities": data.get("activities", []),
                "details": data.get("details", {}),
                "timestamp": data.get("timestamp", 0),
                "count": data.get("count", len(data.get("activities", []))),
            }
        except (json.JSONDecodeError, OSError):
            pass
    return {"activities": [], "details": {}, "timestamp": 0, "count": 0}


def _save_cache(activities: list[dict], details: dict, count: int) -> None:
    try:
        with open(ACTIVITIES_CACHE_FILE, "w") as f:
            json.dump(
                {
                    "activities": activities,
                    "details": details,
                    "timestamp": time.time(),
                    "count": count,
                },
                f,
            )
    except OSError as e:
        print(f"⚠️  Failed to write cache: {e}")


def _cache_satisfies(cache: dict, count: int) -> bool:
    """
    True if the cache is fresh AND holds enough activities to answer `count`.

    Checking `count` matters: a cache built from a 20-activity request cannot
    serve a 50-activity one, and silently returning 20 would look like the
    athlete simply had no older activities.
    """
    if not cache["activities"]:
        return False
    if (time.time() - cache["timestamp"]) >= ACTIVITIES_CACHE_TTL:
        return False
    # Fewer activities than requested is fine only if Strava had no more to give.
    return len(cache["activities"]) >= count or cache["count"] >= count


def _get_summaries(count: int) -> tuple[list[dict], dict]:
    """
    Return (activities, cached_details), fetching from Strava when the cache
    can't answer the request. Falls back to stale cache on API failure.
    """
    cache = _load_cache()
    if _cache_satisfies(cache, count):
        return cache["activities"][:count], cache["details"]

    try:
        token = get_access_token(interactive=False)
        summaries = fetch_activities(token, per_page=count)
    except StravaAuthRequired as e:
        if cache["activities"]:
            print(f"⚠️  Strava auth required ({e}), serving from cache...")
            return cache["activities"][:count], cache["details"]
        raise HTTPException(status_code=401, detail=str(e)) from e
    except StravaRateLimitError as e:
        if cache["activities"]:
            print(f"⚠️  Strava rate limited ({e}), serving from cache...")
            return cache["activities"][:count], cache["details"]
        raise HTTPException(status_code=429, detail=str(e)) from e
    except Exception as e:
        if cache["activities"]:
            print(f"⚠️  Strava API error ({e}), serving from cache...")
            return cache["activities"][:count], cache["details"]
        raise HTTPException(status_code=502, detail=f"Strava error: {e}") from e

    # Keep previously fetched details: they're immutable per activity and each
    # one costs a rate-limited API call to re-fetch.
    _save_cache(summaries, cache["details"], count)
    return summaries, cache["details"]


@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/activities")
def get_activities(count: int = Query(50, ge=1, le=100)):
    """Fetch recent Strava activities with local caching."""
    summaries, details = _get_summaries(count)
    return {"activities": summaries, "details": details}


@app.get("/api/dashboard")
def get_dashboard_data(count: int = Query(50, ge=1, le=100)):
    """Consolidated training metrics, Relative Effort, progression history & Garmin health."""
    summaries, _ = _get_summaries(count)

    # Process activities into weeks with Relative Effort & Volumes
    weeks = process_activities_into_weeks(summaries)
    sorted_week_keys = sorted(weeks.keys())

    # Acute:Chronic Workload Ratio — computed once and shared with the
    # progression history, which would otherwise recalculate it.
    acwr_map = calculate_acwr(sorted_week_keys, weeks)
    for w_key in sorted_week_keys:
        weeks[w_key]["acwr"] = acwr_map.get(w_key, {})

    # Garmin health for active weeks (disk-cached; finished weeks never refetch)
    week_ranges = {
        w_key: (
            date.fromisoformat(weeks[w_key]["week_monday"]),
            date.fromisoformat(weeks[w_key]["week_sunday"]),
        )
        for w_key in sorted_week_keys
    }
    try:
        garmin_summaries = get_weekly_health_summaries(week_ranges)
    except Exception as e:
        print(f"⚠️  Garmin unavailable: {e}")
        garmin_summaries = {}

    return {
        "weeks": weeks,
        "garmin": garmin_summaries,
        "predictions": predict_race_performances(summaries),
        "triathlon": predict_triathlon_performances(summaries),
        "progression": build_progression_history(weeks, acwr_map=acwr_map),
        "activities_total": len(summaries),
    }


@app.post("/api/ai/coach")
def get_ai_coaching_feedback(req: FeedbackRequest):
    """Generate AI coaching analysis and recommendations."""
    try:
        return generate_weekly_coaching_insights(
            week_summary=req.week_summary,
            garmin_health=req.garmin_health,
            athlete_notes=req.athlete_notes,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/sheet/sync")
def sync_google_sheets(req: SyncRequest):
    """Trigger Strava + Garmin sync to Google Sheets."""
    try:
        token = get_access_token(interactive=False)
        summaries = fetch_activities(token, per_page=req.count)

        # Reuse cached details and throttle the rest: a full sync is one API
        # call per activity, against a 200-per-15-minutes limit.
        cache = _load_cache()
        details = fetch_details_for_activities(
            token,
            summaries,
            known_details=cache["details"],
            delay_sec=STRAVA_DETAIL_DELAY_SEC,
        )
        _save_cache(summaries, details, req.count)

        write_to_sheet(summaries, details)
        return {"status": "success", "synced_activities": len(summaries)}
    except StravaAuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except StravaRateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except StravaNetworkError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# Serve Static UI
if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def serve_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Web app frontend building in progress..."}
