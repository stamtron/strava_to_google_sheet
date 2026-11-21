"""
External API Integrations (Strava, Garmin Connect, Final Surge, Telegram, weather).
"""

from .garmin import get_garmin_client, get_weekly_health_summary
from .strava import fetch_activities, fetch_activity_detail, get_access_token

__all__ = [
    "fetch_activities",
    "fetch_activity_detail",
    "get_access_token",
    "get_garmin_client",
    "get_weekly_health_summary",
]
