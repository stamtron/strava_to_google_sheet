"""
External API Integrations (Strava, Garmin Connect, Google Sheets).
"""

from .garmin import get_garmin_client, get_weekly_health_summary
from .sheets import get_sheets_service, write_to_sheet
from .strava import fetch_activities, fetch_activity_detail, get_access_token

__all__ = [
    "fetch_activities",
    "fetch_activity_detail",
    "get_access_token",
    "get_garmin_client",
    "get_sheets_service",
    "get_weekly_health_summary",
    "write_to_sheet",
]
