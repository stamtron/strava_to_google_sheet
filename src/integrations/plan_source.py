"""
Single entry point for "what is planned on this day".

Final Surge (iCal feed) is the plan source when FINAL_SURGE_ICAL_URL is set;
otherwise the Google Sheet is. Callers import from here rather than from a
specific integration so switching sources needs no code changes.
"""

from datetime import date
from typing import Any

from src import config


def final_surge_enabled() -> bool:
    return bool(config.FINAL_SURGE_ICAL_URL)


def get_planned_workout_for_date(target_date: date) -> dict[str, Any]:
    # Module-attribute lookups (not `from ... import`) keep each source patchable in tests.
    if final_surge_enabled():
        from src.integrations import final_surge

        return final_surge.get_planned_workout_for_date(target_date)

    from src.integrations import sheets

    return sheets.get_planned_workout_for_date(target_date)
