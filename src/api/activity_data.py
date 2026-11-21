"""
Where the API gets activities from: a short-TTL JSON cache, Strava, and the
persistent SQLite history store, cheapest first.
"""

import json
import logging
import os
import time

from fastapi import HTTPException

from src.config import ACTIVITIES_CACHE_FILE, ACTIVITIES_CACHE_TTL
from src.integrations.strava import (
    StravaAuthRequired,
    StravaRateLimitError,
    fetch_activities,
    get_access_token,
)
from src.storage.activity_store import get_activities as store_get_activities
from src.storage.activity_store import get_details, init_db, upsert_activities, upsert_details

logger = logging.getLogger(__name__)


def load_cache() -> dict:
    """Return the activities cache, or an empty skeleton if unreadable."""
    if os.path.exists(ACTIVITIES_CACHE_FILE):
        try:
            with open(ACTIVITIES_CACHE_FILE) as f:
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


def save_cache(activities: list[dict], details: dict, count: int) -> None:
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
        logger.warning("Failed to write cache: %s", e)


def cache_satisfies(cache: dict, count: int) -> bool:
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


def read_history(count: int) -> tuple[list[dict], dict]:
    """
    Return (activities, details) from the persistent store, newest first.

    Never raises: the store is a fallback path, so a corrupt or missing DB must
    degrade to "no history" rather than take down an endpoint that could still
    have answered from Strava or the JSON cache.
    """
    try:
        conn = init_db()
    except Exception as e:
        logger.warning("History store unavailable: %s", e)
        return [], {}
    try:
        acts = store_get_activities(conn, limit=count)
        return acts, get_details(conn, [a.get("id") for a in acts])
    except Exception as e:
        logger.warning("History store read failed: %s", e)
        return [], {}
    finally:
        conn.close()


def write_history(activities: list[dict], details: dict | None = None) -> None:
    """Persist freshly fetched activities. Failures here must not fail a request."""
    try:
        conn = init_db()
    except Exception as e:
        logger.warning("History store unavailable: %s", e)
        return
    try:
        upsert_activities(conn, activities)
        if details:
            upsert_details(conn, details)
    except Exception as e:
        logger.warning("History store write failed: %s", e)
    finally:
        conn.close()


def get_summaries(count: int) -> tuple[list[dict], dict]:
    """
    Return (activities, cached_details), fetching from Strava when the cache
    can't answer the request.

    Three tiers, cheapest first: the short-TTL JSON cache, then Strava, then the
    persistent history store. The JSON cache stays in front of the store on
    purpose — it absorbs the dashboard's repeat polling so neither Strava nor
    SQLite is touched on every load.
    """
    cache = load_cache()
    if cache_satisfies(cache, count):
        return cache["activities"][:count], cache["details"]

    def _fallback(reason: str, status: int, exc: Exception):
        if cache["activities"]:
            logger.info("%s, serving from cache...", reason)
            return cache["activities"][:count], cache["details"]
        stored, stored_details = read_history(count)
        if stored:
            logger.info("%s, serving from local history...", reason)
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
    save_cache(summaries, cache["details"], count)
    write_history(summaries)

    # Strava returns at most `count`; anything short means the athlete has no
    # more recent activities, so older stored rows can fill the remainder
    # instead of the window silently shrinking to whatever the last fetch held.
    if len(summaries) < count:
        stored, stored_details = read_history(count)
        if len(stored) > len(summaries):
            seen = {a.get("id") for a in summaries}
            summaries = summaries + [a for a in stored if a.get("id") not in seen]
            summaries = summaries[:count]
            return summaries, {**stored_details, **cache["details"]}

    return summaries, cache["details"]
