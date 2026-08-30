"""
Garmin Connect Integration Module.

Authenticates with Garmin Connect, caches session tokens,
and fetches 24/7 health biometrics (Sleep, Resting HR, HRV).
"""

import os
from datetime import date, timedelta
from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

load_dotenv()

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GARMIN_TOKEN_DIR = os.path.join(os.path.dirname(__file__), ".garmin_tokens")


def get_garmin_client() -> Garmin | None:
    """Authenticate and return an active Garmin client instance."""
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        return None

    try:
        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        os.makedirs(GARMIN_TOKEN_DIR, exist_ok=True)
        client.login(tokenstore=GARMIN_TOKEN_DIR)
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
