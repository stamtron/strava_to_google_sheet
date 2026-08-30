"""
Garmin Connect API Client and Health Biometrics.
"""

import json
import os
import time
from datetime import date, timedelta
from garminconnect import Garmin
from src.config import (
    GARMIN_CACHE_FILE,
    GARMIN_CACHE_TTL,
    GARMIN_EMAIL,
    GARMIN_PASSWORD,
    GARMIN_TOKEN_DIR,
)

# Reused across calls: each login is a full auth round-trip, and the web server
# would otherwise re-authenticate on every dashboard request.
_client: Garmin | None = None
_client_attempted = False


def get_garmin_client(force_new: bool = False) -> Garmin | None:
    """Authenticate and return an active Garmin client instance (memoized)."""
    global _client, _client_attempted

    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        return None

    if not force_new:
        if _client is not None:
            return _client
        # Don't retry a failed login on every request; a bad password would
        # otherwise cost a network round-trip per dashboard load.
        if _client_attempted:
            return None

    _client_attempted = True
    try:
        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        os.makedirs(GARMIN_TOKEN_DIR, exist_ok=True)
        client.login(tokenstore=GARMIN_TOKEN_DIR)
        _client = client
        return client
    except Exception as e:
        print(f"  ⚠️  Garmin Connect login failed: {e}")
        return None


def get_weekly_health_summary(start_date: date, end_date: date, client: Garmin = None) -> dict | None:
    """
    Fetch daily sleep, resting HR, and HRV for a week date range.
    Returns: {
        'total_sleep_h': float | None,
        'avg_sleep_h': float | None,
        'avg_rhr': int | None,
        'avg_hrv': int | None,
    } or None if unavailable.
    """
    if client is None:
        client = get_garmin_client()

    if client is None:
        return None

    total_sleep_seconds = 0
    valid_sleep_days = 0
    rhr_list = []
    hrv_list = []

    curr = start_date
    while curr <= end_date:
        date_str = curr.isoformat()

        # 1. Sleep Data
        try:
            sleep_data = client.get_sleep_data(date_str)
            if sleep_data and "dailySleepDTO" in sleep_data:
                sleep_sec = sleep_data["dailySleepDTO"].get("sleepTimeSeconds")
                if sleep_sec and sleep_sec > 0:
                    total_sleep_seconds += sleep_sec
                    valid_sleep_days += 1
        except Exception:
            pass

        # 2. Resting Heart Rate
        try:
            rhr_data = client.get_rhr_day(date_str)
            rhr_val = None
            if rhr_data:
                metrics = rhr_data.get("allMetrics", {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", [])
                if metrics and isinstance(metrics, list) and len(metrics) > 0:
                    rhr_val = metrics[0].get("value")
                if not rhr_val:
                    rhr_val = rhr_data.get("restingHeartRate")

            if not rhr_val:
                user_summary = client.get_user_summary(date_str)
                if user_summary:
                    rhr_val = user_summary.get("restingHeartRate")

            if rhr_val and rhr_val > 0:
                rhr_list.append(float(rhr_val))
        except Exception:
            pass

        # 3. HRV Data
        try:
            hrv_data = client.get_hrv_data(date_str)
            if hrv_data and "hrvSummary" in hrv_data:
                last_night_avg = hrv_data["hrvSummary"].get("lastNightAvg")
                if last_night_avg and last_night_avg > 0:
                    hrv_list.append(last_night_avg)
        except Exception:
            pass

        curr += timedelta(days=1)

    total_sleep_h = (total_sleep_seconds / 3600.0) if total_sleep_seconds > 0 else None
    avg_sleep_h = (total_sleep_h / valid_sleep_days) if total_sleep_h and valid_sleep_days > 0 else None
    avg_rhr = round(sum(rhr_list) / len(rhr_list)) if rhr_list else None
    avg_hrv = round(sum(hrv_list) / len(hrv_list)) if hrv_list else None

    if not total_sleep_h and not avg_rhr and not avg_hrv:
        return None

    return {
        "total_sleep_h": total_sleep_h,
        "avg_sleep_h": avg_sleep_h,
        "avg_rhr": avg_rhr,
        "avg_hrv": avg_hrv,
    }


def _load_garmin_cache() -> dict:
    if os.path.exists(GARMIN_CACHE_FILE):
        try:
            with open(GARMIN_CACHE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_garmin_cache(cache: dict) -> None:
    try:
        with open(GARMIN_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        print(f"  ⚠️  Failed to write Garmin cache: {e}")


def _cache_entry_is_fresh(entry: dict, week_sunday: date, today: date) -> bool:
    """
    A finished week's biometrics never change, so it stays cached forever.
    Only a week that is still in progress is re-fetched after GARMIN_CACHE_TTL.
    """
    if not isinstance(entry, dict) or "summary" not in entry:
        return False
    if week_sunday < today:
        return True
    return (time.time() - entry.get("fetched_at", 0)) < GARMIN_CACHE_TTL


def get_weekly_health_summaries(week_ranges: dict[str, tuple[date, date]]) -> dict[str, dict]:
    """
    Return {week_key: health_summary} for many weeks, backed by a disk cache.

    Each week costs ~21 Garmin API calls, so an uncached multi-week dashboard is
    hundreds of sequential requests. Cached weeks cost nothing, and the client
    is only created if at least one week actually needs fetching.

    Weeks with no data are cached as an explicit null so they aren't retried on
    every request.
    """
    cache = _load_garmin_cache()
    today = date.today()
    results: dict[str, dict] = {}
    pending: dict[str, tuple[date, date]] = {}

    for week_key, (start, end) in week_ranges.items():
        entry = cache.get(week_key)
        if entry is not None and _cache_entry_is_fresh(entry, end, today):
            if entry["summary"]:
                results[week_key] = entry["summary"]
        else:
            pending[week_key] = (start, end)

    if not pending:
        return results

    client = get_garmin_client()
    if client is None:
        return results

    dirty = False
    for week_key, (start, end) in sorted(pending.items()):
        try:
            summary = get_weekly_health_summary(start, end, client)
        except Exception as e:
            print(f"  ⚠️  Garmin fetch failed for week {week_key}: {e}")
            continue
        cache[week_key] = {"summary": summary, "fetched_at": time.time()}
        dirty = True
        if summary:
            results[week_key] = summary

    if dirty:
        _save_garmin_cache(cache)

    return results
