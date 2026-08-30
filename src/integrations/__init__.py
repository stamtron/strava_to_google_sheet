"""
External API Integrations (Strava, Garmin Connect, Google Sheets).
"""

from .strava import get_access_token, fetch_activities, fetch_activity_detail
from .garmin import get_garmin_client, get_weekly_health_summary
from .sheets import get_sheets_service, write_to_sheet

__all__ = [
    "get_access_token",
    "fetch_activities",
    "fetch_activity_detail",
    "get_garmin_client",
    "get_weekly_health_summary",
    "get_sheets_service",
    "write_to_sheet",
]
