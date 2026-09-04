"""
FastAPI Server for Strava & Garmin Training Dashboard.

Provides REST endpoints for activities, Garmin biometrics,
Relative Effort & ACWR tracking, multi-week progression analytics,
AI coaching, and Google Sheets synchronization.
"""

import json
import os
import time
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import (
    ACTIVITIES_CACHE_FILE,
    ACTIVITIES_CACHE_TTL,
    ALLOWED_ORIGINS,
    AUTO_SYNC_SHEET_ON_WEBHOOK,
    HR_MAX,
    HR_REST,
    STRAVA_DETAIL_DELAY_SEC,
    STRAVA_WEBHOOK_VERIFY_TOKEN,
    WEB_DIR,
)
from src.integrations.strava import (
    StravaAuthRequired,
    StravaNetworkError,
    StravaRateLimitError,
    fetch_activities,
    fetch_activity_detail,
    fetch_details_for_activities,
    get_access_token,
)
from src.integrations.garmin import _load_garmin_cache, get_weekly_health_summaries
from src.integrations.sheets import get_planned_workout_for_date, write_to_sheet
from src.integrations.strava_backfill import backfill_all
from src.integrations.telegram import format_next_day_brief, send_telegram_message
from src.storage.activity_store import (
    count_activities,
    date_range,
    get_activities as store_get_activities,
    get_details,
    init_db,
    upsert_activities,
    upsert_details,
)
from src.analytics.metrics import (
    build_progression_history,
    calculate_acwr,
    calculate_hr_zones,
    calculate_polarized_distribution,
    process_activities_into_weeks,
)
from src.analytics.ai_coach import (
    generate_weekly_coaching_insights,
    predict_race_performances,
    predict_triathlon_performances,
)
from src.analytics.durability import (
    assess_run_durability,
    sport_strength_profile,
    suggest_cross_training,
)
from src.integrations.weather import get_weather_for_date, get_weather_outlook
from src.analytics.coach_agent import (
    CoachUnavailable,
    chat as coach_chat,
    extract_session_facts,
    forget_remembered_fact,
    list_remembered_facts,
)

app = FastAPI(title="Endurance AI Training API", version="2.0.0")

# Explicit origins, credentials off: the API is unauthenticated, so a wildcard
# origin with credentials would let any page the browser visits read this data.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


class FeedbackRequest(BaseModel):
    week_summary: dict
    garmin_health: dict | None = None
    athlete_notes: str = ""


class SyncRequest(BaseModel):
    count: int = 200


class BackfillRequest(BaseModel):
    resume: bool = True


class ChatRequest(BaseModel):
    """One conversational turn. `session_id` is minted server-side on the first."""

    message: str
    session_id: str | None = None
    # The week the athlete has open on the dashboard, so "how was this week?"
    # resolves without them naming a date. Context only — the agent still calls
    # a tool for authoritative numbers.
    week_context: dict | None = None


class MemoryExtractRequest(BaseModel):
    """Which conversation to mine for durable facts."""

    session_id: str


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


def _read_history(count: int) -> tuple[list[dict], dict]:
    """
    Return (activities, details) from the persistent store, newest first.

    Never raises: the store is a fallback path, so a corrupt or missing DB must
    degrade to "no history" rather than take down an endpoint that could still
    have answered from Strava or the JSON cache.
    """
    try:
        conn = init_db()
    except Exception as e:
        print(f"⚠️  History store unavailable: {e}")
        return [], {}
    try:
        acts = store_get_activities(conn, limit=count)
        return acts, get_details(conn, [a.get("id") for a in acts])
    except Exception as e:
        print(f"⚠️  History store read failed: {e}")
        return [], {}
    finally:
        conn.close()


def _write_history(activities: list[dict], details: dict | None = None) -> None:
    """Persist freshly fetched activities. Failures here must not fail a request."""
    try:
        conn = init_db()
    except Exception as e:
        print(f"⚠️  History store unavailable: {e}")
        return
    try:
        upsert_activities(conn, activities)
        if details:
            upsert_details(conn, details)
    except Exception as e:
        print(f"⚠️  History store write failed: {e}")
    finally:
        conn.close()


def _get_summaries(count: int) -> tuple[list[dict], dict]:
    """
    Return (activities, cached_details), fetching from Strava when the cache
    can't answer the request.

    Three tiers, cheapest first: the short-TTL JSON cache, then Strava, then the
    persistent history store. The JSON cache stays in front of the store on
    purpose — it absorbs the dashboard's repeat polling so neither Strava nor
    SQLite is touched on every load.
    """
    cache = _load_cache()
    if _cache_satisfies(cache, count):
        return cache["activities"][:count], cache["details"]

    def _fallback(reason: str, status: int, exc: Exception):
        if cache["activities"]:
            print(f"⚠️  {reason}, serving from cache...")
            return cache["activities"][:count], cache["details"]
        stored, stored_details = _read_history(count)
        if stored:
            print(f"⚠️  {reason}, serving from local history...")
            return stored, stored_details
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    try:
        token = get_access_token(interactive=False)
        summaries = fetch_activities(token, per_page=count)
    except StravaAuthRequired as e:
        return _fallback(f"Strava auth required ({e})", 401, e)
    except StravaRateLimitError as e:
        return _fallback(f"Strava rate limited ({e})", 429, e)
    except Exception as e:
        return _fallback(f"Strava API error ({e})", 502, e)

    # Keep previously fetched details: they're immutable per activity and each
    # one costs a rate-limited API call to re-fetch.
    _save_cache(summaries, cache["details"], count)
    _write_history(summaries)

    # Strava returns at most `count`; anything short means the athlete has no
    # more recent activities, so older stored rows can fill the remainder
    # instead of the window silently shrinking to whatever the last fetch held.
    if len(summaries) < count:
        stored, stored_details = _read_history(count)
        if len(stored) > len(summaries):
            seen = {a.get("id") for a in summaries}
            summaries = summaries + [a for a in stored if a.get("id") not in seen]
            summaries = summaries[:count]
            return summaries, {**stored_details, **cache["details"]}

    return summaries, cache["details"]


@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/activities")
def get_activities(count: int = Query(200, ge=1, le=500)):
    """Fetch recent Strava activities with local caching."""
    summaries, details = _get_summaries(count)
    return {"activities": summaries, "details": details}


@app.get("/api/dashboard")
def get_dashboard_data(count: int = Query(200, ge=1, le=500)):
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
        weeks[w_key]["polarized"] = calculate_polarized_distribution(
            weeks[w_key].get("activities", []),
            hr_max=HR_MAX,
            hr_rest=HR_REST,
        )

    # Garmin health: always return all disk-cached weeks. For uncached weeks,
    # only query the active/recent weeks (at most 1 uncached week per request)
    # so that historical activity queries never block the dashboard on minutes of sequential API calls.
    cached_garmin = _load_garmin_cache()
    active_weeks = set(sorted_week_keys[-2:]) if sorted_week_keys else set()
    week_ranges = {
        w_key: (
            date.fromisoformat(weeks[w_key]["week_monday"]),
            date.fromisoformat(weeks[w_key]["week_sunday"]),
        )
        for w_key in sorted_week_keys
        if w_key in cached_garmin or w_key in active_weeks
    }
    try:
        garmin_summaries = get_weekly_health_summaries(week_ranges, max_fetch=1)
    except Exception as e:
        print(f"⚠️  Garmin unavailable: {e}")
        garmin_summaries = {}

    try:
        weather_outlook = get_weather_outlook(past_days=2, forecast_days=7)
    except Exception as e:
        print(f"⚠️  Weather fetch failed: {e}")
        weather_outlook = []

    return {
        "weeks": weeks,
        "garmin": garmin_summaries,
        "weather": weather_outlook,
        "hr_zones": calculate_hr_zones(hr_max=HR_MAX, hr_rest=HR_REST),
        "predictions": predict_race_performances(summaries),
        "triathlon": predict_triathlon_performances(activities=summaries, use_race_pb=False),
        "triathlon_pb": predict_triathlon_performances(activities=summaries, use_race_pb=True),
        "progression": build_progression_history(weeks, acwr_map=acwr_map),
        "activities_total": len(summaries),
    }


@app.get("/api/strava/webhook")
def strava_webhook_challenge(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Handle Strava Webhook subscription validation handshake.
    Strava sends GET with hub.mode=subscribe, hub.verify_token, hub.challenge.
    """
    if hub_mode == "subscribe" and hub_verify_token == STRAVA_WEBHOOK_VERIFY_TOKEN:
        return {"hub.challenge": hub_challenge}
    raise HTTPException(status_code=403, detail="Invalid verify token.")


@app.post("/api/strava/webhook")
async def strava_webhook_event(event: dict):
    """
    Handle real-time activity events pushed by Strava.
    """
    object_type = event.get("object_type")
    aspect_type = event.get("aspect_type")
    object_id = event.get("object_id")

    if object_type == "activity" and aspect_type in ("create", "update") and object_id:
        try:
            token = get_access_token(interactive=False)
            detail = fetch_activity_detail(int(object_id), token)
            if detail:
                _write_history([detail], {int(object_id): detail})
                cache = _load_cache()
                cache["details"][str(object_id)] = detail
                _save_cache(cache["activities"], cache["details"], cache["count"])

                if AUTO_SYNC_SHEET_ON_WEBHOOK:
                    summaries, details = _get_summaries(30)
                    write_to_sheet(summaries, details=details)
        except Exception as e:
            print(f"⚠️  Webhook activity processing failed: {e}")

    return {"status": "ok"}


class TelegramNextDayRequest(BaseModel):
    target_date: str | None = None  # YYYY-MM-DD, defaults to tomorrow
    custom_tip: str | None = None
    dry_run: bool = False


# Backward compatibility alias
WhatsAppNextDayRequest = TelegramNextDayRequest


@app.post("/api/notifications/telegram/next-day")
def send_telegram_next_day_workout_notification(req: TelegramNextDayRequest = TelegramNextDayRequest()):
    """
    Extract tomorrow's planned workout from Google Sheets, combine with Athens
    weather forecast and AI coaching advice, and dispatch to athlete's Telegram.
    """
    if req.target_date:
        try:
            t_date = date.fromisoformat(req.target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    else:
        t_date = date.today() + timedelta(days=1)

    workout_info = get_planned_workout_for_date(t_date)
    weather_info = get_weather_for_date(t_date)

    # Optional AI tip
    tip = req.custom_tip
    if not tip:
        if weather_info and (weather_info.get("precipitation_mm") or 0) > 2.0:
            tip = "Rain expected; check tire pressure for wet roads or consider indoor trainer."
        elif weather_info and (weather_info.get("temp_max_c") or 0) > 32:
            tip = "High heat expected; hydrate well and start early morning."
        else:
            tip = "Keep easy aerobic pace in Zone 2 for optimal mitochondrial development."

    brief_text = format_next_day_brief(
        target_date=t_date,
        workout_text=workout_info.get("workout_text", ""),
        weather_info=weather_info,
        coach_tip=tip,
        lookup_error=workout_info.get("reason"),
    )

    if req.dry_run:
        return {
            "success": True,
            "target_date": t_date.isoformat(),
            "preview": brief_text,
            "workout_info": workout_info,
            "weather_info": weather_info,
            "provider": "dry-run",
        }

    dispatch_res = send_telegram_message(brief_text)
    return {
        "success": dispatch_res.get("success", False),
        "target_date": t_date.isoformat(),
        "preview": brief_text,
        "dispatch": dispatch_res,
        "workout_info": workout_info,
    }


@app.post("/api/notifications/whatsapp/next-day", deprecated=True)
def send_next_day_workout_notification_deprecated(req: TelegramNextDayRequest = TelegramNextDayRequest()):
    """Deprecated endpoint: forwarded to Telegram dispatcher."""
    return send_telegram_next_day_workout_notification(req)


@app.get("/api/weather")
def get_weather(
    past_days: int = Query(3, ge=0, le=14),
    forecast_days: int = Query(7, ge=1, le=14),
    refresh: bool = Query(False),
):
    """Daily historical and forecast weather for the athlete's home city (Athens, Greece)."""
    try:
        outlook = get_weather_outlook(
            past_days=past_days,
            forecast_days=forecast_days,
            force_refresh=refresh,
        )
        return {"city": "Athens, Greece", "days": outlook}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/durability")
def get_run_durability(count: int = Query(200, ge=1, le=500)):
    """
    Run-specific load management: ramp rate, spacing, long-run share, monotony,
    and the cross-training plan that keeps the aerobic dose while lowering impact.

    Separate from `/api/dashboard` because it answers a different question. The
    dashboard reports what was done; this reports whether the legs can take more.
    """
    summaries, _ = _get_summaries(count)

    weeks = process_activities_into_weeks(summaries)
    sorted_week_keys = sorted(weeks.keys())
    acwr_run = calculate_acwr(sorted_week_keys, weeks, effort_key="run_relative_effort")

    durability = assess_run_durability(weeks, acwr_run)
    profile = sport_strength_profile(summaries)

    # The target is the athlete's own recent run load, not an arbitrary goal:
    # the question being answered is "can I keep doing what I'm doing?"
    recent = [weeks[k].get("run_relative_effort", 0.0) for k in sorted_week_keys[-4:]]
    target_run_load = round(max(recent), 1) if recent else 0.0

    return {
        "durability": durability,
        "profile": profile,
        "acwr_run": acwr_run,
        "cross_training": suggest_cross_training(target_run_load, durability, profile),
        "weeks_analyzed": len(sorted_week_keys),
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


@app.post("/api/ai/chat")
def post_ai_chat(req: ChatRequest):
    """
    Answer one turn of conversation with the tool-using coach.

    Separate from `/api/ai/coach`, which stays a stateless one-shot generator for
    the weekly panel and keeps its heuristic fallback. This endpoint has no
    fallback on purpose: a conversational answer assembled from heuristics would
    be worse than an honest 503.
    """
    try:
        return coach_chat(
            message=req.message,
            session_id=req.session_id,
            week_context=req.week_context,
        )
    except CoachUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/coach/memory")
def get_coach_memory():
    """
    List every durable fact the coach has stored about the athlete.

    The memory outlives any conversation, so it has to be inspectable: this is
    how you find out that the coach is still working around an injury you
    recovered from months ago.
    """
    try:
        facts = list_remembered_facts()
        return {"facts": facts, "count": len(facts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/api/coach/memory/{fact_id}")
def delete_coach_memory(fact_id: str):
    """Prune one stored fact. 404 if that id was never stored."""
    try:
        removed = forget_remembered_fact(fact_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not removed:
        raise HTTPException(status_code=404, detail=f"No stored fact with id {fact_id}")
    return {"deleted": fact_id}


@app.post("/api/coach/memory/extract")
def post_coach_memory_extract(req: MemoryExtractRequest):
    """
    Mine a finished conversation for durable facts and file them.

    Called when the athlete starts a new chat: the closing conversation is the
    last chance to keep what they mentioned in passing before the transcript
    ages out. Facts land with `source="auto"` so they can be told apart from the
    ones they asked the coach to remember.
    """
    try:
        return extract_session_facts(req.session_id)
    except CoachUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/history/status")
def history_status():
    """Report what the local history store currently holds."""
    conn = init_db()
    try:
        span = date_range(conn)
        return {
            "total_activities": count_activities(conn),
            "oldest": span[0].isoformat() if span else None,
            "newest": span[1].isoformat() if span else None,
        }
    finally:
        conn.close()


@app.post("/api/history/backfill")
def history_backfill(req: BackfillRequest):
    """
    Import the full Strava activity history into the local store.

    Long-running on a first run — one request per page of 200 activities — but
    idempotent, and resumable if Strava rate-limits partway through.
    """
    try:
        token = get_access_token(interactive=False)
    except StravaAuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    conn = init_db()
    try:
        return backfill_all(token, conn, resume=req.resume)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        conn.close()


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
