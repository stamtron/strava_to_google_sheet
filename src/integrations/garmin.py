"""
Garmin Connect API Client and Health Biometrics.
"""

import json
import logging
import math
import os
import re
import time
import unicodedata
from datetime import date, timedelta

from garminconnect import Garmin, GarminConnectAuthenticationError

from src.config import (
    BASELINE_LOW_FLOOR,
    BASELINE_MIN_WEEKS,
    BASELINE_STD_FACTOR,
    BASELINE_STD_FLOOR,
    GARMIN_CACHE_FILE,
    GARMIN_CACHE_TTL,
    GARMIN_EMAIL,
    GARMIN_EMPTY_CACHE_TTL,
    GARMIN_LOGIN_RETRY_COOLDOWN_SEC,
    GARMIN_PASSWORD,
    GARMIN_TOKEN_DIR,
    RECOVERY_ADEQUATE_MIN,
    RECOVERY_BASE_SCORE,
    RECOVERY_BB_DEPLETED,
    RECOVERY_BB_HIGH,
    RECOVERY_BB_MODERATE,
    RECOVERY_COMPROMISED_MIN,
    RECOVERY_OPTIMAL_MIN,
    RECOVERY_RHR_HIGH_BPM,
    RECOVERY_RHR_LOW_BPM,
    RECOVERY_SCORE_MAX,
    RECOVERY_SCORE_MIN,
    RECOVERY_SLEEP_CRITICAL_H,
    RECOVERY_SLEEP_GOOD_H,
    RECOVERY_SLEEP_LOW_H,
    RECOVERY_UNKNOWN_SCORE,
)
from src.timeutil import today_local

logger = logging.getLogger(__name__)

# Reused across calls: each login is a full auth round-trip, and the web server
# would otherwise re-authenticate on every dashboard request.
_client: Garmin | None = None
_client_failed_at: float | None = None


def invalidate_garmin_client() -> None:
    """Drop the memoized client (e.g. after an auth error) so the next call logs in again."""
    global _client
    _client = None


def get_garmin_client(force_new: bool = False) -> Garmin | None:
    """Authenticate and return an active Garmin client instance (memoized)."""
    global _client, _client_failed_at

    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        return None

    if not force_new:
        if _client is not None:
            return _client
        # Don't retry a failed login on every request (a bad password would
        # cost a round-trip per dashboard load), but do retry after a cooldown
        # so a transient outage at startup doesn't disable Garmin until restart.
        if _client_failed_at is not None and time.time() - _client_failed_at < GARMIN_LOGIN_RETRY_COOLDOWN_SEC:
            return None

    try:
        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        os.makedirs(GARMIN_TOKEN_DIR, exist_ok=True)
        client.login(tokenstore=GARMIN_TOKEN_DIR)
    except Exception as e:
        _client_failed_at = time.time()
        logger.error("Garmin Connect login failed: %s", e)
        return None
    _client = client
    _client_failed_at = None
    return client


def _record_call_error(errors: list, what: str, date_str: str, e: Exception) -> None:
    """Note a failed Garmin endpoint call; an auth error invalidates the cached client."""
    logger.debug("Garmin %s fetch failed for %s: %s", what, date_str, e)
    errors.append(e)
    if isinstance(e, GarminConnectAuthenticationError):
        invalidate_garmin_client()


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

    summary, _errors = _fetch_weekly_health_summary(start_date, end_date, client)
    return summary


def _fetch_weekly_health_summary(start_date: date, end_date: date, client: Garmin) -> tuple[dict | None, list]:
    """Fetch a week's biometrics; also return the endpoint errors hit along the way."""
    errors: list = []
    total_sleep_seconds = 0
    total_nap_seconds = 0
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

        # 1. Sleep & Nap Data
        try:
            sleep_data = client.get_sleep_data(date_str)
            if sleep_data and "dailySleepDTO" in sleep_data:
                dto = sleep_data["dailySleepDTO"]
                sleep_sec = dto.get("sleepTimeSeconds") or 0
                nap_sec = dto.get("napTimeSeconds") or 0
                day_total_sleep = sleep_sec + nap_sec
                if day_total_sleep > 0:
                    total_sleep_seconds += day_total_sleep
                    total_nap_seconds += nap_sec
                    valid_sleep_days += 1
        except Exception as e:
            _record_call_error(errors, "sleep", date_str, e)

        # 2. Resting Heart Rate & User Summary
        user_summary = None
        try:
            user_summary = client.get_user_summary(date_str)
        except Exception as e:
            _record_call_error(errors, "user summary", date_str, e)

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
        except Exception as e:
            _record_call_error(errors, "RHR", date_str, e)

        # 3. HRV Data
        try:
            hrv_data = client.get_hrv_data(date_str)
            if hrv_data and "hrvSummary" in hrv_data:
                last_night_avg = hrv_data["hrvSummary"].get("lastNightAvg")
                if last_night_avg and last_night_avg > 0:
                    hrv_list.append(last_night_avg)
        except Exception as e:
            _record_call_error(errors, "HRV", date_str, e)

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
        except Exception as e:
            _record_call_error(errors, "stress", date_str, e)

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
        except Exception as e:
            _record_call_error(errors, "body battery", date_str, e)

        # 6. Training Readiness
        try:
            readiness_data = client.get_training_readiness(date_str)
            if readiness_data and "score" in readiness_data:
                readiness_list.append(float(readiness_data["score"]))
            elif user_summary and "trainingReadinessScore" in user_summary:
                readiness_list.append(float(user_summary["trainingReadinessScore"]))
        except Exception as e:
            _record_call_error(errors, "training readiness", date_str, e)

        curr += timedelta(days=1)

    total_sleep_h = (total_sleep_seconds / 3600.0) if total_sleep_seconds > 0 else None
    avg_sleep_h = (total_sleep_h / valid_sleep_days) if total_sleep_h and valid_sleep_days > 0 else None
    total_nap_h = (total_nap_seconds / 3600.0) if total_nap_seconds > 0 else None
    avg_rhr = round(sum(rhr_list) / len(rhr_list)) if rhr_list else None
    avg_hrv = round(sum(hrv_list) / len(hrv_list)) if hrv_list else None
    avg_stress = round(sum(stress_list) / len(stress_list)) if stress_list else None
    avg_bb_charged = round(sum(body_battery_charged) / len(body_battery_charged)) if body_battery_charged else None
    avg_bb_drained = round(sum(body_battery_drained) / len(body_battery_drained)) if body_battery_drained else None
    avg_readiness = round(sum(readiness_list) / len(readiness_list)) if readiness_list else None

    if errors:
        logger.warning(
            "Garmin week %s..%s: %d endpoint call(s) failed (first: %s)",
            start_date, end_date, len(errors), errors[0],
        )

    if not total_sleep_h and not avg_rhr and not avg_hrv and not avg_stress and not avg_bb_charged:
        return None, errors

    return {
        "total_sleep_h": total_sleep_h,
        "avg_sleep_h": avg_sleep_h,
        "total_nap_h": total_nap_h,
        "avg_rhr": avg_rhr,
        "avg_hrv": avg_hrv,
        "avg_stress": avg_stress,
        "avg_bb_charged": avg_bb_charged,
        "avg_bb_drained": avg_bb_drained,
        "avg_readiness": avg_readiness,
    }, errors


def _load_garmin_cache() -> dict:
    if os.path.exists(GARMIN_CACHE_FILE):
        try:
            with open(GARMIN_CACHE_FILE) as f:
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
        logger.warning("Failed to write Garmin cache: %s", e)


def _cache_entry_is_fresh(entry: dict, week_sunday: date, today: date) -> bool:
    """
    A finished week's biometrics never change, so it stays cached forever.
    Only a week that is still in progress is re-fetched after GARMIN_CACHE_TTL.
    Empty or partial weeks (no data yet, or some endpoint calls failed) are
    re-checked after GARMIN_EMPTY_CACHE_TTL so a late watch sync or a transient
    outage doesn't leave a permanent hole.
    """
    if not isinstance(entry, dict) or "summary" not in entry:
        return False
    age = time.time() - entry.get("fetched_at", 0)
    if not entry["summary"] or entry.get("partial"):
        return age < GARMIN_EMPTY_CACHE_TTL
    if week_sunday < today:
        return True
    return age < GARMIN_CACHE_TTL


def get_weekly_health_summaries(
    week_ranges: dict[str, tuple[date, date]],
    max_fetch: int | None = None,
) -> dict[str, dict]:
    """
    Return {week_key: health_summary} for many weeks, backed by a disk cache.

    Each week costs ~21 Garmin API calls, so an uncached multi-week dashboard is
    hundreds of sequential requests. Cached weeks cost nothing, and the client
    is only created if at least one week actually needs fetching.

    Weeks with no data are cached as an explicit null (and weeks where some
    calls failed are flagged partial) for GARMIN_EMPTY_CACHE_TTL, so they aren't
    retried on every request but aren't frozen forever either.
    """
    cache = _load_garmin_cache()
    today = today_local()
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
            summary, errors = _fetch_weekly_health_summary(start, end, client)
        except Exception as e:
            logger.warning("Garmin fetch failed for week %s: %s", week_key, e)
            continue
        if summary is None and errors:
            # Nothing came back because the calls failed: don't record "no data".
            continue
        entry = {"summary": summary, "fetched_at": time.time()}
        if errors:
            entry["partial"] = True
        cache[week_key] = entry
        _save_garmin_cache(cache)
        if summary:
            results[week_key] = summary

    return results


def compute_biometric_baselines(
    weekly_summaries: dict[str, dict] | None = None,
    min_weeks: int = BASELINE_MIN_WEEKS,
) -> dict:
    """
    Compute athlete baseline normal ranges for HRV and Resting Heart Rate (RHR)
    using multi-week historical summaries (mean ± BASELINE_STD_FACTOR * std).
    """
    if weekly_summaries is None:
        cached = _load_garmin_cache()
        weekly_summaries = {
            k: v["summary"] for k, v in cached.items() if isinstance(v, dict) and v.get("summary")
        }

    hrv_values = []
    rhr_values = []

    # Sort weeks chronologically
    for _, summary in sorted(weekly_summaries.items()):
        if not summary:
            continue
        hrv = summary.get("avg_hrv")
        if hrv and hrv > 0:
            hrv_values.append(float(hrv))
        rhr = summary.get("avg_rhr")
        if rhr and rhr > 0:
            rhr_values.append(float(rhr))

    def _calc_band(values: list[float], factor: float = BASELINE_STD_FACTOR) -> dict:
        if len(values) < min_weeks:
            return {
                "baseline_mean": round(sum(values) / len(values), 1) if values else None,
                "baseline_std": None,
                "normal_range": None,
                "weeks_used": len(values),
            }
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        std = math.sqrt(variance)
        # Minimum sensible std floor to avoid zero-width bands on identical readings
        std = max(std, BASELINE_STD_FLOOR)
        low = round(max(BASELINE_LOW_FLOOR, mean - factor * std), 1)
        high = round(mean + factor * std, 1)
        return {
            "baseline_mean": round(mean, 1),
            "baseline_std": round(std, 1),
            "normal_range": [low, high],
            "weeks_used": len(values),
        }

    return {
        "hrv": _calc_band(hrv_values),
        "rhr": _calc_band(rhr_values),
    }


def evaluate_biometric_status(val: float | None, band: dict, metric_type: str = "hrv") -> str:
    """
    Evaluate a metric reading against its baseline band.
    Returns 'optimal' | 'low' | 'elevated' | 'unknown'; display text lives in
    src/status_labels.biometric_description.
    """
    if val is None or not band or not band.get("normal_range"):
        return "unknown"

    low, high = band["normal_range"]
    if val < low:
        return "low"
    if val > high:
        return "elevated"
    return "optimal"


def get_daily_recovery_metrics(target_date: date, client: Garmin | None = None) -> dict:
    """
    Fetch comprehensive daily recovery biometrics for a specific date from Garmin Connect.

    Returns:
    {
        'target_date': str (YYYY-MM-DD),
        'available': bool,
        'sleep_seconds': int | None,
        'sleep_hours': float | None,
        'sleep_score': int | None,
        'resting_hr': int | None,
        'hrv_last_night': int | None,
        'hrv_status': str,
        'body_battery_charged': int | None,
        'body_battery_drained': int | None,
        'body_battery_latest': int | None,
        'avg_stress': int | None,
        'recovery_score': int,
        'recovery_status': 'optimal' | 'adequate' | 'compromised' | 'poor' | 'unknown',
        'flags': list[str]
    }
    """
    if client is None:
        client = get_garmin_client()

    date_str = target_date.isoformat()
    res = {
        "target_date": date_str,
        "available": False,
        "sleep_seconds": None,
        "sleep_hours": None,
        "sleep_score": None,
        "resting_hr": None,
        "hrv_last_night": None,
        "hrv_status": "unknown",
        "body_battery_charged": None,
        "body_battery_drained": None,
        "body_battery_latest": None,
        "avg_stress": None,
        "recovery_score": RECOVERY_UNKNOWN_SCORE,
        "recovery_status": "unknown",
        "flags": [],
    }

    if client is None:
        return res

    has_data = False
    errors: list = []

    # 1. Sleep data
    try:
        sleep_data = client.get_sleep_data(date_str)
        if sleep_data and "dailySleepDTO" in sleep_data:
            dto = sleep_data["dailySleepDTO"]
            sec = dto.get("sleepTimeSeconds") or 0
            nap = dto.get("napTimeSeconds") or 0
            total_sec = sec + nap
            if total_sec > 0:
                res["sleep_seconds"] = total_sec
                res["sleep_hours"] = round(total_sec / 3600.0, 1)
                has_data = True

            # Extract sleep score if available
            scores = dto.get("sleepScores") or {}
            overall = scores.get("overall") or {}
            if isinstance(overall, dict) and overall.get("value"):
                res["sleep_score"] = int(overall["value"])
    except Exception as e:
        _record_call_error(errors, "daily sleep", date_str, e)

    # 2. Resting Heart Rate & User Summary
    user_summary = None
    try:
        user_summary = client.get_user_summary(date_str)
    except Exception as e:
        _record_call_error(errors, "daily user summary", date_str, e)

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
            res["resting_hr"] = round(float(rhr_val))
            has_data = True
    except Exception as e:
        _record_call_error(errors, "daily RHR", date_str, e)

    # 3. Overnight HRV
    try:
        hrv_data = client.get_hrv_data(date_str)
        if hrv_data and "hrvSummary" in hrv_data:
            summary = hrv_data["hrvSummary"]
            last_night = summary.get("lastNightAvg")
            status = summary.get("status")
            if last_night and last_night > 0:
                res["hrv_last_night"] = round(last_night)
                has_data = True
            if status:
                res["hrv_status"] = str(status).lower()
    except Exception as e:
        _record_call_error(errors, "daily HRV", date_str, e)

    # 4. Stress Data
    try:
        stress_data = client.get_stress_data(date_str)
        stress_val = None
        if stress_data:
            stress_val = stress_data.get("avgStressLevel")
        if not stress_val and user_summary:
            stress_val = user_summary.get("averageStressLevel")
        if stress_val and float(stress_val) > 0:
            res["avg_stress"] = round(float(stress_val))
            has_data = True
    except Exception as e:
        _record_call_error(errors, "daily stress", date_str, e)

    # 5. Body Battery
    try:
        bb_data = client.get_body_battery(date_str)
        if bb_data and isinstance(bb_data, list) and len(bb_data) > 0:
            entry = bb_data[0]
            ch = entry.get("charged")
            dr = entry.get("drained")
            if ch is not None:
                res["body_battery_charged"] = int(ch)
                has_data = True
            if dr is not None:
                res["body_battery_drained"] = int(dr)
                has_data = True

            # Most recent battery level in the day
            values = entry.get("bodyBatteryValuesArray") or []
            if values and len(values) > 0:
                res["body_battery_latest"] = int(values[-1][1])
        elif user_summary:
            ch = user_summary.get("bodyBatteryChargedValue")
            dr = user_summary.get("bodyBatteryDrainedValue")
            if ch is not None:
                res["body_battery_charged"] = int(ch)
                has_data = True
            if dr is not None:
                res["body_battery_drained"] = int(dr)
                has_data = True
    except Exception as e:
        _record_call_error(errors, "daily body battery", date_str, e)

    if errors:
        logger.warning("Garmin daily recovery %s: %d endpoint call(s) failed (first: %s)", date_str, len(errors), errors[0])

    if not has_data:
        return res

    res["available"] = True

    # Compute composite recovery score and flags
    flags = []
    score = RECOVERY_BASE_SCORE

    # Sleep impact
    sleep_h = res.get("sleep_hours")
    if sleep_h is not None:
        if sleep_h < RECOVERY_SLEEP_CRITICAL_H:
            score -= 30
            flags.append("critically_low_sleep")
        elif sleep_h < RECOVERY_SLEEP_LOW_H:
            score -= 15
            flags.append("low_sleep")
        elif sleep_h >= RECOVERY_SLEEP_GOOD_H:
            score += 10

    # HRV & RHR Baselines comparison
    baselines = compute_biometric_baselines()
    hrv_eval = evaluate_biometric_status(res.get("hrv_last_night"), baselines.get("hrv", {}), "hrv")
    rhr_eval = evaluate_biometric_status(res.get("resting_hr"), baselines.get("rhr", {}), "rhr")
    res["baselines"] = baselines
    res["hrv_baseline_eval"] = {
        "status": hrv_eval,
        "value": res.get("hrv_last_night"),
        "normal_range": baselines.get("hrv", {}).get("normal_range"),
    }
    res["rhr_baseline_eval"] = {
        "status": rhr_eval,
        "value": res.get("resting_hr"),
        "normal_range": baselines.get("rhr", {}).get("normal_range"),
    }

    # HRV impact
    hrv_stat = res.get("hrv_status", "").lower()
    if hrv_stat in ("poor", "low", "unbalanced") or hrv_eval == "low":
        score -= 25
        flags.append("depressed_hrv")
    elif hrv_stat in ("balanced", "good") or hrv_eval == "optimal":
        score += 10

    # RHR impact
    rhr = res.get("resting_hr")
    if rhr is not None:
        if rhr > RECOVERY_RHR_HIGH_BPM or rhr_eval == "elevated":
            score -= 15
            flags.append("elevated_rhr")
        elif rhr <= RECOVERY_RHR_LOW_BPM or rhr_eval == "low":
            score += 5

    # Body battery impact
    bb = res.get("body_battery_latest") or res.get("body_battery_charged")
    if bb is not None:
        if bb < RECOVERY_BB_DEPLETED:
            score -= 20
            flags.append("depleted_body_battery")
        elif bb < RECOVERY_BB_MODERATE:
            score -= 10
            flags.append("moderate_body_battery")
        elif bb >= RECOVERY_BB_HIGH:
            score += 10

    final_score = max(RECOVERY_SCORE_MIN, min(RECOVERY_SCORE_MAX, score))
    res["recovery_score"] = final_score
    res["flags"] = flags

    if final_score >= RECOVERY_OPTIMAL_MIN:
        res["recovery_status"] = "optimal"
    elif final_score >= RECOVERY_ADEQUATE_MIN:
        res["recovery_status"] = "adequate"
    elif final_score >= RECOVERY_COMPROMISED_MIN:
        res["recovery_status"] = "compromised"
    else:
        res["recovery_status"] = "poor"

    return res


def _fold_accents(text: str) -> str:
    """Lowercase and strip combining accents so 'Ξεκούραση' matches 'ξεκουραση'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# Whole-word rest markers: substring matching made "90'' rest" between intervals,
# "interest" or "offroad" read as a rest day.
_REST_DAY_RE = re.compile(r"\b(?:rest|off|ξεκουραση|ρεπο)\b")
# Rep notation such as "6x400m" / "4 × 8'".
_REPS_RE = re.compile(r"\d\s*[x×]\s*\d")
_HIGH_INTENSITY_KEYWORDS = (
    "διαλειμματικη",
    "διαλειμματα",
    "tempo",
    "κατωφλι",
    "threshold",
    "vo2",
    "strides",
    "ανοιγματα",
    "repeat",
    "1000m",
    "400m",
    "800m",
    "2000m",
    "z4",
    "z5",
    "interval",
)


def assess_workout_readiness(recovery: dict, workout_text: str = "") -> dict:
    """
    Correlate athlete's physiological recovery status with the day's workout prescription.

    If recovery is compromised or poor and the planned session includes high-intensity
    intervals, tempo, or threshold work, picks a modulation `advice_key`
    (rendered by src/status_labels.readiness_advice).
    """
    target_date = recovery.get("target_date")
    status = recovery.get("recovery_status", "unknown")
    score = recovery.get("recovery_score", RECOVERY_UNKNOWN_SCORE)
    flags = recovery.get("flags", [])

    clean_text = _fold_accents(workout_text or "")

    # Detect intensity first: a structured session that mentions rest between
    # reps (e.g. "6x400m, 90'' rest") is not a rest day.
    is_high_intensity = (
        any(k in clean_text for k in _HIGH_INTENSITY_KEYWORDS)
        or bool(_REPS_RE.search(clean_text))
        or any(p in clean_text for p in ("@ 3:", "@ 4:", "@ 3,", "@ 4,"))
    )
    is_structured = is_high_intensity or "@" in clean_text
    is_rest = not clean_text.strip() or (not is_structured and bool(_REST_DAY_RE.search(clean_text)))

    needs_modulation = False
    advice_key = "normal"
    substitute_option = None

    if is_rest:
        advice_key = "rest_day"
    elif status == "poor":
        needs_modulation = True
        if is_high_intensity:
            advice_key = "poor_high_intensity"
            substitute_option = {
                "kind": "recovery_ride",
                "sport": "Cycling",
                "duration_min": 50,
                "target_zone": "z1_z2",
            }
        else:
            advice_key = "poor_easy"
    elif status == "compromised":
        if is_high_intensity:
            needs_modulation = True
            advice_key = "compromised_high_intensity"
            substitute_option = {"kind": "reduce_intensity", "pace_adjustment_sec": 12}
        else:
            advice_key = "compromised_easy"
    elif status == "optimal":
        advice_key = "optimal"

    return {
        "date": target_date,
        "recovery_status": status,
        "recovery_score": score,
        "flags": flags,
        "workout_detected_intensity": "high" if is_high_intensity else ("rest" if is_rest else "moderate"),
        "needs_modulation": needs_modulation,
        "advice_key": advice_key,
        "substitute_option": substitute_option,
    }
