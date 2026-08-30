"""Cache-correctness tests for the API layer and the Garmin week cache."""

import time
from datetime import date, timedelta

from src.api.server import _cache_satisfies
from src.config import ACTIVITIES_CACHE_TTL, GARMIN_CACHE_TTL
from src.integrations.garmin import _cache_entry_is_fresh


def _cache(n_activities: int, count: int, age_sec: float = 0.0) -> dict:
    return {
        "activities": [{"id": i} for i in range(n_activities)],
        "details": {},
        "timestamp": time.time() - age_sec,
        "count": count,
    }


def test_fresh_cache_with_enough_activities_is_used():
    assert _cache_satisfies(_cache(50, 50), 50)
    assert _cache_satisfies(_cache(50, 50), 20)


def test_cache_built_from_a_smaller_request_cannot_serve_a_larger_one():
    """The original bug: a 20-activity cache silently answered a 50 request."""
    assert not _cache_satisfies(_cache(20, 20), 50)


def test_short_cache_is_reused_when_strava_had_no_more_to_give():
    # Asked for 50, Strava only had 20 -> 20 is the complete answer.
    assert _cache_satisfies(_cache(20, 50), 50)


def test_expired_cache_is_rejected():
    assert not _cache_satisfies(_cache(50, 50, age_sec=ACTIVITIES_CACHE_TTL + 1), 10)


def test_empty_cache_is_rejected():
    assert not _cache_satisfies(_cache(0, 0), 1)


def test_finished_garmin_week_is_cached_indefinitely():
    today = date(2026, 8, 30)
    entry = {"summary": {"avg_rhr": 48}, "fetched_at": time.time() - 10 * GARMIN_CACHE_TTL}
    assert _cache_entry_is_fresh(entry, today - timedelta(days=1), today)


def test_in_progress_garmin_week_expires_with_the_ttl():
    today = date(2026, 8, 30)
    week_sunday = today + timedelta(days=1)
    stale = {"summary": {"avg_rhr": 48}, "fetched_at": time.time() - GARMIN_CACHE_TTL - 1}
    fresh = {"summary": {"avg_rhr": 48}, "fetched_at": time.time()}
    assert not _cache_entry_is_fresh(stale, week_sunday, today)
    assert _cache_entry_is_fresh(fresh, week_sunday, today)


def test_garmin_null_summary_still_counts_as_cached():
    """Weeks with no biometrics must not be refetched on every request."""
    today = date(2026, 8, 30)
    entry = {"summary": None, "fetched_at": time.time()}
    assert _cache_entry_is_fresh(entry, today, today)


def test_malformed_garmin_entry_is_not_fresh():
    today = date(2026, 8, 30)
    assert not _cache_entry_is_fresh({}, today - timedelta(days=7), today)
    assert not _cache_entry_is_fresh({"fetched_at": time.time()}, today, today)
