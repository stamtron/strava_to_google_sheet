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
    total_nap_h = (total_nap_seconds / 3600.0) if total_nap_seconds > 0 else None
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
        "total_nap_h": total_nap_h,
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
        "recovery_score": 75,
        "recovery_status": "unknown",
        "flags": [],
    }

    if client is None:
        return res

    has_data = False

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
            res["resting_hr"] = int(round(float(rhr_val)))
            has_data = True
    except Exception:
        pass

    # 3. Overnight HRV
    try:
        hrv_data = client.get_hrv_data(date_str)
        if hrv_data and "hrvSummary" in hrv_data:
            summary = hrv_data["hrvSummary"]
            last_night = summary.get("lastNightAvg")
            status = summary.get("status")
            if last_night and last_night > 0:
                res["hrv_last_night"] = int(round(last_night))
                has_data = True
            if status:
                res["hrv_status"] = str(status).lower()
    except Exception:
        pass

    # 4. Stress Data
    try:
        stress_data = client.get_stress_data(date_str)
        stress_val = None
        if stress_data:
            stress_val = stress_data.get("avgStressLevel")
        if not stress_val and user_summary:
            stress_val = user_summary.get("averageStressLevel")
        if stress_val and float(stress_val) > 0:
            res["avg_stress"] = int(round(float(stress_val)))
            has_data = True
    except Exception:
        pass

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
    except Exception:
        pass

    if not has_data:
        return res

    res["available"] = True

    # Compute composite recovery score and flags
    flags = []
    score = 80  # Baseline

    # Sleep impact
    sleep_h = res.get("sleep_hours")
    if sleep_h is not None:
        if sleep_h < 5.5:
            score -= 30
            flags.append("critically_low_sleep")
        elif sleep_h < 6.5:
            score -= 15
            flags.append("low_sleep")
        elif sleep_h >= 7.5:
            score += 10

    # HRV impact
    hrv_stat = res.get("hrv_status", "").lower()
    if hrv_stat in ("poor", "low", "unbalanced"):
        score -= 25
        flags.append("depressed_hrv")
    elif hrv_stat in ("balanced", "good"):
        score += 10

    # RHR impact
    rhr = res.get("resting_hr")
    if rhr is not None:
        if rhr > 60:
            score -= 15
            flags.append("elevated_rhr")
        elif rhr <= 50:
            score += 5

    # Body battery impact
    bb = res.get("body_battery_latest") or res.get("body_battery_charged")
    if bb is not None:
        if bb < 30:
            score -= 20
            flags.append("depleted_body_battery")
        elif bb < 50:
            score -= 10
            flags.append("moderate_body_battery")
        elif bb >= 75:
            score += 10

    final_score = max(10, min(100, score))
    res["recovery_score"] = final_score
    res["flags"] = flags

    if final_score >= 80:
        res["recovery_status"] = "optimal"
    elif final_score >= 65:
        res["recovery_status"] = "adequate"
    elif final_score >= 45:
        res["recovery_status"] = "compromised"
    else:
        res["recovery_status"] = "poor"

    return res


def assess_workout_readiness(recovery: dict, workout_text: str = "") -> dict:
    """
    Correlate athlete's physiological recovery status with the day's workout prescription.
    
    If recovery is compromised or poor and the planned session includes high-intensity
    intervals, tempo, or threshold work, generates concrete modulation advice.
    """
    target_date = recovery.get("target_date")
    status = recovery.get("recovery_status", "unknown")
    score = recovery.get("recovery_score", 75)
    flags = recovery.get("flags", [])

    clean_text = (workout_text or "").lower()

    # Detect workout intensity
    is_rest = not clean_text or any(w in clean_text for w in ("ξεκουραση", "rest", "off", "ρεπο"))
    high_intensity_keywords = (
        "διαλειμματικη",
        "διαλειμματα",
        "tempo",
        "κατωφλι",
        "threshold",
        "vo2",
        "strides",
        "ανοίγματα",
        "repeat",
        "1000m",
        "400m",
        "800m",
        "2000m",
        "z4",
        "z5",
        "interval",
    )
    is_high_intensity = any(k in clean_text for k in high_intensity_keywords) or "@ 3:" in clean_text or "@ 4:" in clean_text or "@ 3," in clean_text or "@ 4," in clean_text
    is_run = any(k in clean_text for k in ("τρεξιμο", "δρομικες", "run", "χαλαρο"))

    needs_modulation = False
    advice = "Εκτέλεσε την προπόνηση κανονικά σύμφωνα με τις οδηγίες του προπονητή."
    substitute_option = None

    if is_rest:
        advice = "Ημέρα ξεκούρασης/αποφόρτισης. Επικεντρώσου στον ύπνο και τη σωστή διατροφή."
    elif status == "poor":
        needs_modulation = True
        if is_high_intensity:
            advice = (
                "⚠️ *Κόκκινος Δείκτης Αποκατάστασης (Poor Recovery):* Η φυσιολογική κόπωση είναι υψηλή "
                f"({', '.join(flags) if flags else 'χαμηλή ετοιμότητα'}). Συνιστάται πλήρης ακύρωση των έντονων "
                "διαλειμμάτων και αντικατάσταση με 40' χαλαρό αερόβιο τρέξιμο (Zone 1-2) ή 50' χαλαρό ποδήλατο Tacx."
            )
            substitute_option = {
                "sport": "Cycling",
                "duration_min": 50,
                "target_zone": "Z1-Z2 Recovery",
                "notes": "Χωρίς αντίσταση, 85-90 rpm cadence για μυϊκή ανακούφιση.",
            }
        else:
            advice = (
                "⚠️ *Χαμηλή Αποκατάσταση:* Διατήρησε τον ρυθμό αυστηρά στην καρδιακή Zone 1/2. "
                "Μην πιέσεις για ρυθμό αν οι καρδιακοί παλμοί ανεβαίνουν γρήγορα."
            )
    elif status == "compromised":
        if is_high_intensity:
            needs_modulation = True
            advice = (
                "🟡 *Μέτρια Αποκατάσταση (Compromised):* Τα βιομετρικά δείχνουν μερική κόπωση "
                f"({', '.join(flags) if flags else 'μη βέλτιστο HRV/ύπνος'}). Συνιστάται μείωση της έντασης "
                "στα διαστήματα κατά 10–15 δευτ/χλμ ή μείωση των επαναλήψεων (π.χ. 4 αντί για 6) "
                "ώστε να μην υπερφορτωθεί το καρδιαγγειακό σύστημα."
            )
            substitute_option = {
                "pace_adjustment_sec": 12,
                "suggested_action": "Reduce intensity by 10-15s/km or cut 1-2 intervals",
            }
        else:
            advice = "Αποδεκτή αποκατάσταση. Εκτέλεσε το αερόβιο πρόγραμμα δίνοντας έμφαση στην ενυδάτωση."
    elif status == "optimal":
        advice = "🟢 *Βέλτιστη Ετοιμότητα (Optimal Readiness):* Οργανισμός πλήρως αναρρωμένος! Έτοιμος για υψηλή ένταση."

    return {
        "date": target_date,
        "recovery_status": status,
        "recovery_score": score,
        "flags": flags,
        "workout_detected_intensity": "high" if is_high_intensity else ("rest" if is_rest else "moderate"),
        "needs_modulation": needs_modulation,
        "modulation_advice": advice,
        "substitute_option": substitute_option,
    }
