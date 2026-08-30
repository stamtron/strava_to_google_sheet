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

from src.config import ACTIVITIES_CACHE_FILE, WEB_DIR
from src.integrations.strava import fetch_activities, fetch_activity_detail, get_access_token
from src.integrations.garmin import get_garmin_client, get_weekly_health_summary
from src.integrations.sheets import write_to_sheet
from src.analytics.metrics import process_activities_into_weeks, build_progression_history, calculate_acwr
from src.analytics.ai_coach import generate_weekly_coaching_insights, predict_race_performances

app = FastAPI(title="Endurance AI Training API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FeedbackRequest(BaseModel):
    week_summary: dict
    garmin_health: dict | None = None
    athlete_notes: str = ""


class SyncRequest(BaseModel):
    count: int = 35


def _load_cache() -> tuple[list[dict], dict, float] | None:
    if os.path.exists(ACTIVITIES_CACHE_FILE):
        try:
            with open(ACTIVITIES_CACHE_FILE, "r") as f:
                data = json.load(f)
                return data.get("activities", []), data.get("details", {}), data.get("timestamp", 0)
        except Exception:
            pass
    return None


def _save_cache(activities: list[dict], details: dict):
    try:
        with open(ACTIVITIES_CACHE_FILE, "w") as f:
            json.dump({"activities": activities, "details": details, "timestamp": time.time()}, f)
    except Exception as e:
        print(f"⚠️  Failed to write cache: {e}")


@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/activities")
def get_activities(count: int = Query(50, ge=1, le=100)):
    """Fetch recent Strava activities with local caching."""
    cached = _load_cache()
    if cached and (time.time() - cached[2] < 600):
        return {"activities": cached[0][:count], "details": cached[1]}

    try:
        token = get_access_token()
        summaries = fetch_activities(token, per_page=count)
        _save_cache(summaries, cached[1] if cached else {})
        return {"activities": summaries, "details": {}}
    except Exception as e:
        if cached:
            print(f"⚠️  Strava API error ({e}), serving activities from cache...")
            return {"activities": cached[0][:count], "details": cached[1]}
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/dashboard")
def get_dashboard_data(count: int = Query(50, ge=1, le=100)):
    """Consolidated training metrics, Relative Effort, progression history & Garmin health."""
    cached = _load_cache()
    summaries = []

    if cached and (time.time() - cached[2] < 600):
        summaries = cached[0]
    else:
        try:
            token = get_access_token()
            summaries = fetch_activities(token, per_page=count)
            _save_cache(summaries, cached[1] if cached else {})
        except Exception as e:
            if cached:
                print(f"⚠️  Strava API error ({e}), serving dashboard from cache...")
                summaries = cached[0]
            else:
                raise HTTPException(status_code=500, detail=f"Strava error: {e}")

    # Process activities into weeks with Relative Effort & Volumes
    weeks = process_activities_into_weeks(summaries)
    sorted_week_keys = sorted(weeks.keys())

    # Acute:Chronic Workload Ratio (ACWR)
    acwr_map = calculate_acwr(sorted_week_keys, weeks)
    for w_key in sorted_week_keys:
        weeks[w_key]["acwr"] = acwr_map.get(w_key, {})

    # Fetch Garmin health for active weeks
    garmin_client = get_garmin_client()
    garmin_summaries = {}
    if garmin_client:
        for week_key, wdata in weeks.items():
            m_date = date.fromisoformat(wdata["week_monday"])
            s_date = date.fromisoformat(wdata["week_sunday"])
            try:
                gh = get_weekly_health_summary(m_date, s_date, garmin_client)
                if gh:
                    garmin_summaries[week_key] = gh
            except Exception:
                pass

    # Race predictions
    predictions = predict_race_performances(summaries)

    # Multi-week progression history
    progression = build_progression_history(weeks)

    return {
        "weeks": weeks,
        "garmin": garmin_summaries,
        "predictions": predictions,
        "progression": progression,
        "activities_total": len(summaries),
    }


@app.post("/api/ai/coach")
def get_ai_coaching_feedback(req: FeedbackRequest):
    """Generate AI coaching analysis and recommendations."""
    try:
        insights = generate_weekly_coaching_insights(
            week_summary=req.week_summary,
            garmin_health=req.garmin_health,
            athlete_notes=req.athlete_notes,
        )
        return insights
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sheet/sync")
def sync_google_sheets(req: SyncRequest):
    """Trigger Strava + Garmin sync to Google Sheets."""
    try:
        token = get_access_token()
        summaries = fetch_activities(token, per_page=req.count)
        details = {}
        for act in summaries:
            act_id = act.get("id")
            if act_id:
                details[act_id] = fetch_activity_detail(token, act_id)

        write_to_sheet(summaries, details)
        return {"status": "success", "synced_activities": len(summaries)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Serve Static UI
if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def serve_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Web app frontend building in progress..."}
