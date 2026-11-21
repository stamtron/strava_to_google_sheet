"""
Final Surge training plan integration via the private iCal calendar feed.

Final Surge publishes every planned workout on a read-only iCalendar feed
(Settings → Calendar Sync). The feed is fetched, cached on disk, and parsed
into per-day workout text so compliance, Telegram briefs, the coach and the Garmin workout push
can consume it.
"""

import json
import logging
import os
import time
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

from src import config
from src.storage.atomic_file import write_json_atomic

logger = logging.getLogger(__name__)


def _feed_url() -> str:
    """The configured feed URL, with webcal:// rewritten to https://."""
    url = config.FINAL_SURGE_ICAL_URL
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    return url


def _redact(err: Exception) -> str:
    """
    Error text with the private feed key removed.

    requests embeds either the full URL or only its path (e.g. connection-pool
    errors), so the key — the last path segment — is redacted on its own too.
    """
    text = str(err)
    url = _feed_url()
    key = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    for secret in (config.FINAL_SURGE_ICAL_URL, url, key):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def _load_cache() -> dict[str, Any] | None:
    if not os.path.exists(config.FINAL_SURGE_CACHE_FILE):
        return None
    try:
        with open(config.FINAL_SURGE_CACHE_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Ignoring unreadable Final Surge cache: %s", e)
        return None
    if isinstance(data, dict) and isinstance(data.get("ics"), str):
        return data
    return None


def fetch_calendar_text(force_refresh: bool = False) -> str:
    """
    Return the raw iCal feed, served from the disk cache while it is fresh.

    A failed download falls back to a stale cache rather than reporting an
    empty plan; with no cache at all the error propagates.
    """
    url = _feed_url()
    if not url:
        raise ValueError("FINAL_SURGE_ICAL_URL is not configured.")

    cached = _load_cache()
    if (
        cached
        and not force_refresh
        and time.time() - cached.get("fetched_at", 0) < config.FINAL_SURGE_CACHE_TTL
    ):
        return cached["ics"]

    try:
        resp = requests.get(url, timeout=config.FINAL_SURGE_HTTP_TIMEOUT_SEC)
        resp.raise_for_status()
        ics = resp.text
        if "BEGIN:VCALENDAR" not in ics:
            raise ValueError("Final Surge feed did not return an iCalendar document.")
    except (requests.RequestException, ValueError) as e:
        if cached:
            logger.warning("Final Surge fetch failed, using stale cache: %s", _redact(e))
            return cached["ics"]
        raise ConnectionError(f"Final Surge fetch failed: {_redact(e)}") from None

    write_json_atomic(config.FINAL_SURGE_CACHE_FILE, {"fetched_at": time.time(), "ics": ics})
    return ics


def _event_date(value: date | datetime) -> date:
    """All-day events are dates; timed events are converted to athlete-local dates."""
    if isinstance(value, datetime):
        tz = ZoneInfo(config.ATHLETE_TIMEZONE)
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(tz).date()
    return value


def _event_text(summary: str, description: str) -> str:
    summary = summary.strip()
    description = description.strip()
    if not description:
        return summary
    if not summary or description.startswith(summary):
        return description
    return f"{summary}\n{description}"


def parse_calendar(ics_text: str) -> dict[date, list[dict[str, Any]]]:
    """Group feed events by athlete-local date, in start-time order."""
    cal = Calendar.from_ical(ics_text)
    by_day: dict[date, list[tuple[str, dict[str, Any]]]] = {}

    for event in cal.walk("VEVENT"):
        dtstart = event.get("DTSTART")
        if dtstart is None:
            continue
        start = dtstart.dt
        day = _event_date(start)
        summary = str(event.get("SUMMARY", ""))
        description = str(event.get("DESCRIPTION", ""))
        text = _event_text(summary, description)
        if not text:
            continue
        sort_key = start.isoformat() if isinstance(start, datetime) else ""
        by_day.setdefault(day, []).append(
            (sort_key, {"uid": str(event.get("UID", "")), "summary": summary.strip(), "text": text})
        )

    return {day: [item for _, item in sorted(items, key=lambda x: x[0])] for day, items in by_day.items()}


# Final Surge event titles (the workout type) → internal sport keys.
_SPORT_BY_TITLE = {
    "run": "running",
    "bike": "cycling",
    "swim": "swim",
    "strength training": "strength",
    "rest day": "rest",
}


def event_sport(workout: dict[str, Any]) -> str | None:
    """Sport key for a feed event from its title, or None for unknown types."""
    return _SPORT_BY_TITLE.get(workout.get("summary", "").strip().lower())


def has_plan_details(workout: dict[str, Any]) -> bool:
    """
    True when the event carries an actual prescription.

    Bare entries (title plus "Workout: <type>" only) appear alongside planned
    sessions and would otherwise become empty Garmin workouts.
    """
    summary = workout.get("summary", "").strip().lower()
    for line in workout.get("text", "").splitlines():
        line = line.strip().lower()
        if line and line != summary and not line.startswith("workout:"):
            return True
    return False


def get_workouts_by_date(force_refresh: bool = False) -> dict[date, list[dict[str, Any]]]:
    return parse_calendar(fetch_calendar_text(force_refresh=force_refresh))


def day_workout_text(workouts: list[dict[str, Any]]) -> str:
    """Join a day's workouts into one plan text block."""
    return "\n\n".join(w["text"] for w in workouts)


def get_planned_workout_for_date(target_date: date) -> dict[str, Any]:
    """Planned workout for target_date, with found/workout_text/reason keys."""
    base = {"date": target_date.isoformat(), "source": "final_surge"}
    try:
        workouts = get_workouts_by_date().get(target_date, [])
    except Exception as e:
        msg = _redact(e)
        logger.error("Failed to read Final Surge plan: %s", msg)
        return {**base, "found": False, "workout_text": "", "reason": f"Failed to read Final Surge plan: {msg}"}

    text = day_workout_text(workouts)
    result = {**base, "found": bool(text), "workout_text": text, "workouts": workouts}
    if not text:
        result["reason"] = f"No Final Surge workout scheduled on {target_date.isoformat()}"
    return result
