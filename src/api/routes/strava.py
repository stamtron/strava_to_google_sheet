"""Strava: webhook handshake and events, manual sync, and full-history backfill."""

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from src.api.activity_data import load_cache, save_cache, write_history
from src.api.deps import internal_error
from src.config import (
    STRAVA_DETAIL_DELAY_SEC,
    STRAVA_WEBHOOK_VERIFY_TOKEN,
    STRAVA_WEBHOOK_VERIFY_TOKEN_PLACEHOLDER,
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
from src.integrations.strava_backfill import backfill_all
from src.storage.activity_store import count_activities, date_range, init_db

logger = logging.getLogger(__name__)
router = APIRouter()


class SyncRequest(BaseModel):
    count: int = 200


class BackfillRequest(BaseModel):
    resume: bool = True


@router.get("/api/strava/webhook")
def strava_webhook_challenge(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Handle Strava Webhook subscription validation handshake.
    Strava sends GET with hub.mode=subscribe, hub.verify_token, hub.challenge.
    """
    # The shipped placeholder is public, so accepting it would let anyone
    # complete a subscription handshake against this server.
    if not STRAVA_WEBHOOK_VERIFY_TOKEN or STRAVA_WEBHOOK_VERIFY_TOKEN == STRAVA_WEBHOOK_VERIFY_TOKEN_PLACEHOLDER:
        logger.warning(
            "Rejected Strava webhook handshake: STRAVA_WEBHOOK_VERIFY_TOKEN is unset or the default placeholder."
        )
        raise HTTPException(status_code=403, detail="Webhook verify token is not configured.")
    if hub_mode == "subscribe" and hub_verify_token == STRAVA_WEBHOOK_VERIFY_TOKEN:
        return {"hub.challenge": hub_challenge}
    raise HTTPException(status_code=403, detail="Invalid verify token.")


def _process_strava_webhook_activity(object_id: int) -> None:
    """Fetch activity details and persist them in a background worker."""
    try:
        token = get_access_token(interactive=False)
        detail = fetch_activity_detail(int(object_id), token)
        if detail:
            write_history([detail], {int(object_id): detail})
            cache = load_cache()
            cache["details"][str(object_id)] = detail
            save_cache(cache["activities"], cache["details"], cache["count"])
    except Exception as e:
        logger.warning("Webhook activity processing failed for %s: %s", object_id, e)


@router.post("/api/strava/webhook")
async def strava_webhook_event(
    event: dict,
    background_tasks: BackgroundTasks,
    secret: str | None = Query(None),
):
    """
    Handle real-time activity events pushed by Strava.
    Returns status: ok immediately; processes details via BackgroundTasks without blocking.
    """
    from src.config import STRAVA_WEBHOOK_SECRET_PARAM

    # Fail closed: an unset secret would accept events from anyone.
    if not STRAVA_WEBHOOK_SECRET_PARAM:
        logger.warning("Rejected Strava webhook event: STRAVA_WEBHOOK_SECRET_PARAM is not configured.")
        raise HTTPException(status_code=403, detail="Webhook secret is not configured.")
    if not secret or not hmac.compare_digest(secret, STRAVA_WEBHOOK_SECRET_PARAM):
        logger.warning("Rejected Strava webhook event: invalid secret.")
        raise HTTPException(status_code=403, detail="Invalid webhook secret.")

    object_type = event.get("object_type")
    aspect_type = event.get("aspect_type")
    object_id = event.get("object_id")

    if object_type == "activity" and aspect_type in ("create", "update") and object_id:
        background_tasks.add_task(_process_strava_webhook_activity, int(object_id))

    return {"status": "ok"}


@router.get("/api/history/status")
def history_status():
    """Report what the local history store currently holds."""
    try:
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
    except Exception as e:
        logger.exception("History status failed")
        raise HTTPException(status_code=500, detail="History store is unavailable.") from e


@router.post("/api/history/backfill")
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
        raise internal_error("History backfill") from e
    finally:
        conn.close()


@router.post("/api/sync")
def sync_activities(req: SyncRequest):
    """Pull recent Strava activities + details into the cache and local history store."""
    try:
        token = get_access_token(interactive=False)
        summaries = fetch_activities(token, per_page=req.count)

        # Reuse cached details and throttle the rest: a full sync is one API
        # call per activity, against a 200-per-15-minutes limit.
        cache = load_cache()
        details = fetch_details_for_activities(
            token,
            summaries,
            known_details=cache["details"],
            delay_sec=STRAVA_DETAIL_DELAY_SEC,
        )
        save_cache(summaries, details, req.count)
        write_history(summaries, details)
        return {"status": "success", "synced_activities": len(summaries)}
    except StravaAuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except StravaRateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except StravaNetworkError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise internal_error("Activity sync") from e
