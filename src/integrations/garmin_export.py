"""
Seed the Garmin biometrics cache from a Garmin Connect data export.

The GDPR export carries the same daily wellness data the live API serves:

- `DI-Connect-Aggregator/UDSFile_*.json`: daily summaries (resting HR, all-day
  stress, Body Battery charged/drained)
- `DI-Connect-Wellness/*_sleepData.json`: nightly sleep stages and naps
- `DI-Connect-Wellness/*_healthStatusData.json`: overnight HRV average

Days are merged by calendar date, grouped into Monday-keyed weeks and averaged
with `garmin.summarize_health_days`, the same aggregation the API fetch uses,
then written into `.garmin_cache.json`. That gives the biometric baselines and
the dashboard health charts months of history without ~21 API calls per week.

The export is untrusted user input: files are size-capped, only JSON is parsed
(never executed), and every field is type-checked before use.
"""

import glob
import json
import logging
import os
import time
from datetime import date, timedelta

from src.config import GARMIN_EXPORT_MAX_JSON_MB
from src.integrations.garmin import _load_garmin_cache, _save_garmin_cache, summarize_health_days

logger = logging.getLogger(__name__)

_MAX_BYTES = int(GARMIN_EXPORT_MAX_JSON_MB * 1024 * 1024)
EXPORT_SOURCE = "garmin_export"


def _find(root: str, folder: str, pattern: str) -> list[str]:
    """Files matching `pattern` inside any `folder` directory under `root`."""
    return sorted(glob.glob(os.path.join(root, "**", folder, pattern), recursive=True))


def _load_list(path: str, stats: dict) -> list:
    """Parse a JSON array file; oversized or malformed files are counted and skipped."""
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            stats["skipped_files"] += 1
            return []
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug("Skipping %s: %s", path, exc)
        stats["skipped_files"] += 1
        return []
    if not isinstance(data, list):
        stats["skipped_files"] += 1
        return []
    stats["files"] += 1
    return [item for item in data if isinstance(item, dict)]


def _calendar_date(item: dict) -> str | None:
    raw = item.get("calendarDate")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _uds_fields(item: dict) -> dict:
    day = {"rhr": _num(item.get("restingHeartRate"))}
    stress = item.get("allDayStress")
    if isinstance(stress, dict):
        for agg in stress.get("aggregatorList") or []:
            if isinstance(agg, dict) and agg.get("type") == "TOTAL":
                day["stress"] = _num(agg.get("averageStressLevel"))
    battery = item.get("bodyBattery")
    if isinstance(battery, dict):
        day["bb_charged"] = _num(battery.get("chargedValue"))
        day["bb_drained"] = _num(battery.get("drainedValue"))
    return day


def _sleep_fields(item: dict) -> dict:
    stages = [_num(item.get(k)) for k in ("deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds")]
    naps = item.get("napList") if isinstance(item.get("napList"), list) else []
    nap_s = sum(_num(n.get("napTimeSec")) or 0.0 for n in naps if isinstance(n, dict))
    return {"sleep_s": sum(s for s in stages if s), "nap_s": nap_s}


def _health_fields(item: dict) -> dict:
    for metric in item.get("metrics") or []:
        if isinstance(metric, dict) and metric.get("type") == "HRV":
            return {"hrv": _num(metric.get("value"))}
    return {}


def read_export_days(root: str) -> tuple[dict[str, dict], dict]:
    """
    Daily biometrics from an export: ({"YYYY-MM-DD": day dict}, stats).
    Day dicts use the keys `summarize_health_days` expects.
    """
    stats = {"files": 0, "skipped_files": 0, "days": 0}
    days: dict[str, dict] = {}
    sources = (
        ("DI-Connect-Aggregator", "UDSFile_*.json", _uds_fields),
        ("DI-Connect-Wellness", "*_sleepData.json", _sleep_fields),
        ("DI-Connect-Wellness", "*_healthStatusData.json", _health_fields),
    )
    for folder, pattern, extract in sources:
        for path in _find(root, folder, pattern):
            for item in _load_list(path, stats):
                day_key = _calendar_date(item)
                if day_key is None:
                    continue
                fields = {k: v for k, v in extract(item).items() if v is not None}
                days.setdefault(day_key, {}).update(fields)
    stats["days"] = len(days)
    return days, stats


def weekly_summaries_from_days(days: dict[str, dict]) -> dict[str, dict]:
    """Group daily biometrics into {monday_iso: weekly summary}, dropping empty weeks."""
    weeks: dict[str, list[dict]] = {}
    for day_key, day in days.items():
        d = date.fromisoformat(day_key)
        monday = (d - timedelta(days=d.weekday())).isoformat()
        weeks.setdefault(monday, []).append(day)
    out = {}
    for monday, week_days in sorted(weeks.items()):
        summary = summarize_health_days(week_days)
        if summary:
            out[monday] = summary
    return out


def import_garmin_wellness(root: str, today: date) -> dict:
    """
    Merge export weeks into the Garmin cache and return import stats.

    Complete weeks already fetched from the API are kept as-is; missing, empty
    or partial cache entries are filled from the export. The current week is
    skipped so the live API keeps refreshing it.
    """
    if not os.path.isdir(root):
        raise FileNotFoundError(root)
    days, stats = read_export_days(root)
    weekly = weekly_summaries_from_days(days)
    current_monday = (today - timedelta(days=today.weekday())).isoformat()

    cache = _load_garmin_cache()
    written = kept = 0
    for monday, summary in weekly.items():
        if monday >= current_monday:
            continue
        entry = cache.get(monday)
        if isinstance(entry, dict) and entry.get("summary") and not entry.get("partial"):
            kept += 1
            continue
        cache[monday] = {"summary": summary, "fetched_at": time.time(), "source": EXPORT_SOURCE}
        written += 1
    if written:
        _save_garmin_cache(cache)
    stats.update({"weeks": len(weekly), "weeks_written": written, "weeks_kept": kept})
    return stats
