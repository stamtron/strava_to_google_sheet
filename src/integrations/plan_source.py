"""
Single entry point for "what is planned on this day".

The plan lives in Final Surge (private iCal feed, FINAL_SURGE_ICAL_URL).
Callers import from here rather than from the integration directly so the
plan source can be swapped without touching Telegram, the coach or the API.
"""

from datetime import date
from typing import Any


def get_planned_workout_for_date(target_date: date) -> dict[str, Any]:
    # Module-attribute lookup (not `from ... import`) keeps the source patchable in tests.
    from src.integrations import final_surge

    return final_surge.get_planned_workout_for_date(target_date)


def get_plan_texts_between(start: date, end: date) -> dict[date, str]:
    from src.integrations import final_surge

    return final_surge.get_plan_texts_between(start, end)
