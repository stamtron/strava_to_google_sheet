"""
FastAPI Server for Strava & Garmin Training Dashboard.

Provides REST endpoints for activities, Garmin biometrics,
AI coaching feedback, race predictions, and Google Sheets synchronization.
"""

import os
from datetime import date, datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ai_coach import generate_weekly_coaching_insights, predict_race_performances
from garmin_client import get_garmin_client, get_weekly_health_summary
from google_sheets import write_to_sheet
from main import fetch_activities, fetch_activity_detail
from strava_auth import get_access_token

app = FastAPI(title="Strava & Garmin Training Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
os.makedirs(WEB_DIR, exist_ok=True)


class FeedbackRequest(BaseModel):
    week_summary: dict
    garmin_health: dict | None = None
    athlete_notes: str = ""


class SyncRequest(BaseModel):
    count: int = 30


ACTIVITIES_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".activities_cache.json")


def _load_cache() -> tuple[list[dict], dict, float] | None:
    if os.path.exists(ACTIVITIES_CACHE_FILE):
        try:
            import json
            with open(ACTIVITIES_CACHE_FILE, "r") as f:
                data = json.load(f)
                return data.get("activities", []), data.get("details", {}), data.get("timestamp", 0)
        except Exception:
            pass
    return None


def _save_cache(activities: list[dict], details: dict):
    try:
        import json
        import time
        with open(ACTIVITIES_CACHE_FILE, "w") as f:
            json.dump({"activities": activities, "details": details, "timestamp": time.time()}, f)
    except Exception as e:
        print(f"⚠️  Failed to write cache: {e}")


@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/activities")
def get_activities(count: int = Query(30, ge=1, le=100)):
    """Fetch recent Strava activities with caching and rate limit fallback."""
    import time
    cached = _load_cache()
    if cached:
        activities, details, ts = cached
        if time.time() - ts < 600:  # Fresh within 10 mins
            return {"activities": activities[:count], "details": details}

    try:
        token = get_access_token()
        summaries = fetch_activities(token, per_page=count)
        _save_cache(summaries, {})
        return {"activities": summaries, "details": {}}
    except Exception as e:
        if cached:
            print(f"⚠️  Strava API error ({e}), serving from cache...")
            return {"activities": cached[0][:count], "details": cached[1]}
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/dashboard")
def get_dashboard_data(count: int = Query(35, ge=1, le=100)):
    """Return consolidated dashboard data: weekly totals, Garmin biometrics, and workouts."""
    import time
    cached = _load_cache()
    summaries = []

    # Check cache freshness
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
                raise HTTPException(status_code=500, detail=f"Strava error and no cache available: {e}")

    # Group activities by week Monday
    weeks = {}
    for act in summaries:
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
                "bike_dist_km": 0.0,
                "bike_time_sec": 0,
                "swim_dist_m": 0.0,
                "swim_time_sec": 0,
                "strength_time_sec": 0,
                "total_time_sec": 0,
            }

        weeks[week_key]["activities"].append(act)
        moving_time = act.get("moving_time", 0) or 0
        dist_km = (act.get("distance", 0) or 0) / 1000.0
        sport = act.get("sport_type") or act.get("type", "")

        weeks[week_key]["total_time_sec"] += moving_time

        if sport in ["Run", "TrailRun"]:
            weeks[week_key]["run_dist_km"] += dist_km
            weeks[week_key]["run_time_sec"] += moving_time
        elif sport in ["Ride", "VirtualRide", "GravelRide", "MountainBikeRide"]:
            if act.get("trainer", False) and dist_km < 0.1 and moving_time > 0:
                dist_km = (moving_time / 3600.0) * 21.0
            weeks[week_key]["bike_dist_km"] += dist_km
            weeks[week_key]["bike_time_sec"] += moving_time
        elif sport in ["Swim"]:
            dist_m = (dist_km * 1000.0) / 2.0
            weeks[week_key]["swim_dist_m"] += dist_m
            weeks[week_key]["swim_time_sec"] += moving_time
        elif sport in ["WeightTraining", "Workout"]:
            weeks[week_key]["strength_time_sec"] += moving_time

    # Fetch Garmin data for active weeks
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

    # Calculate race predictions based on recent run activities
    predictions = predict_race_performances(summaries)

    return {
        "weeks": weeks,
        "garmin": garmin_summaries,
        "predictions": predictions,
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


# Serve static web frontend
if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def serve_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Web app frontend building in progress..."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
