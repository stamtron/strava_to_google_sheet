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
    stress_list = []
    body_battery_charged = []
    body_battery_drained = []
    readiness_list = []

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

        # 2. Resting Heart Rate & User Summary
        user_summary = None
        try:
            user_summary = client.get_user_summary(date_str)
        except Exception:
            pass

        try:
            rhr_data = client.get_rhr_day(date_str)
            rhr_val = None
            if rhr_data:
                metrics = rhr_data.get("allMetrics", {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", [])
                if metrics and isinstance(metrics, list) and len(metrics) > 0:
                    rhr_val = metrics[0].get("value")
                if not rhr_val:
                    rhr_val = rhr_data.get("restingHeartRate")

            if not rhr_val and user_summary:
                rhr_val = user_summary.get("restingHeartRate")

            if rhr_val and float(rhr_val) > 0:
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

        # 4. Stress Data
        try:
            stress_data = client.get_stress_data(date_str)
            stress_avg = None
            if stress_data:
                stress_avg = stress_data.get("avgStressLevel")
            if not stress_avg and user_summary:
                stress_avg = user_summary.get("averageStressLevel")
            if stress_avg and float(stress_avg) > 0:
                stress_list.append(float(stress_avg))
        except Exception:
            pass

        # 5. Body Battery Data
        try:
            bb_data = client.get_body_battery(date_str)
            if bb_data and isinstance(bb_data, list) and len(bb_data) > 0:
                bb_entry = bb_data[0]
                charged = bb_entry.get("charged")
                drained = bb_entry.get("drained")
                if charged is not None:
                    body_battery_charged.append(float(charged))
                if drained is not None:
                    body_battery_drained.append(float(drained))
            elif user_summary:
                ch = user_summary.get("bodyBatteryChargedValue")
                dr = user_summary.get("bodyBatteryDrainedValue")
                if ch is not None:
                    body_battery_charged.append(float(ch))
                if dr is not None:
                    body_battery_drained.append(float(dr))
        except Exception:
            pass

        # 6. Training Readiness
        try:
            readiness_data = client.get_training_readiness(date_str)
            if readiness_data and "score" in readiness_data:
                readiness_list.append(float(readiness_data["score"]))
            elif user_summary and "trainingReadinessScore" in user_summary:
                readiness_list.append(float(user_summary["trainingReadinessScore"]))
        except Exception:
            pass

        curr += timedelta(days=1)

    total_sleep_h = (total_sleep_seconds / 3600.0) if total_sleep_seconds > 0 else None
    avg_sleep_h = (total_sleep_h / valid_sleep_days) if total_sleep_h and valid_sleep_days > 0 else None
    avg_rhr = round(sum(rhr_list) / len(rhr_list)) if rhr_list else None
    avg_hrv = round(sum(hrv_list) / len(hrv_list)) if hrv_list else None
    avg_stress = round(sum(stress_list) / len(stress_list)) if stress_list else None
    avg_bb_charged = round(sum(body_battery_charged) / len(body_battery_charged)) if body_battery_charged else None
    avg_bb_drained = round(sum(body_battery_drained) / len(body_battery_drained)) if body_battery_drained else None
    avg_readiness = round(sum(readiness_list) / len(readiness_list)) if readiness_list else None

    if not total_sleep_h and not avg_rhr and not avg_hrv and not avg_stress and not avg_bb_charged:
        return None

    return {
        "total_sleep_h": total_sleep_h,
        "avg_sleep_h": avg_sleep_h,
        "avg_rhr": avg_rhr,
        "avg_hrv": avg_hrv,
        "avg_stress": avg_stress,
        "avg_bb_charged": avg_bb_charged,
        "avg_bb_drained": avg_bb_drained,
        "avg_readiness": avg_readiness,
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


def get_weekly_health_summaries(
    week_ranges: dict[str, tuple[date, date]],
    max_fetch: int | None = None,
) -> dict[str, dict]:
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

    # Fetch newest weeks first so recent training sees biometrics first
    items_to_fetch = sorted(pending.items(), reverse=True)
    if max_fetch is not None and max_fetch > 0:
        items_to_fetch = items_to_fetch[:max_fetch]

    for week_key, (start, end) in items_to_fetch:
        try:
            summary = get_weekly_health_summary(start, end, client)
        except Exception as e:
            print(f"  ⚠️  Garmin fetch failed for week {week_key}: {e}")
            continue
        cache[week_key] = {"summary": summary, "fetched_at": time.time()}
        _save_garmin_cache(cache)
        if summary:
            results[week_key] = summary

    return results
