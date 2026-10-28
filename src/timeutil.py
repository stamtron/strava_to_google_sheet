"""Athlete-local clock helpers.

Activity dates come from Strava's ``start_date_local`` (athlete wall clock),
so "today" must be evaluated in ``ATHLETE_TIMEZONE`` rather than the host's
local time — otherwise a UTC server shifts days around midnight Athens time.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.config import ATHLETE_TIMEZONE


def athlete_tz() -> ZoneInfo:
    return ZoneInfo(ATHLETE_TIMEZONE)


def now_local() -> datetime:
    """Timezone-aware current datetime in the athlete's timezone."""
    return datetime.now(athlete_tz())


def today_local() -> date:
    """Current calendar date in the athlete's timezone."""
    return now_local().date()
