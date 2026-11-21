"""
Import runs, rides and swims from a Garmin Connect data export into the history store.

Garmin's GDPR export ships the original activity uploads as FIT files inside
`DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_*.zip`, alongside thousands
of daily monitoring files. This walks a directory, zip or single FIT file,
decodes each member in memory (nothing is extracted to disk) and stores:

- runs with a distance stream, with per-km splits (`fit_runs`), so the
  decoupling trend can reach back past the Strava backfill;
- a summary of every run, ride and swim (`fit_activities`: duration, distance,
  average HR, ride power) for the long-range multi-sport trends.

The export is user-supplied and untrusted: members are size-capped, and a file
that fails to decode is counted and skipped rather than aborting the import.
"""

import logging
import os
import sqlite3
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise

from src.analytics.fit_splits import moving_seconds, splits_from_records
from src.config import FIT_MAX_FILE_MB, FIT_MAX_RECORD_GAP_S
from src.integrations.fit_file import FitError, fit_activity_summary, read_fit
from src.storage.activity_store import upsert_fit_activities, upsert_fit_runs
from src.timeutil import athlete_tz

logger = logging.getLogger(__name__)

_MAX_BYTES = int(FIT_MAX_FILE_MB * 1024 * 1024)

# FIT sport name -> stats key, for the sports kept as trend summaries.
TREND_SPORTS = {"running": "runs", "cycling": "rides", "swimming": "swims"}


def _iter_fit_blobs(path: str) -> Iterator[tuple[str, bytes | None]]:
    """Yield (name, bytes) for every FIT file under `path`; bytes is None if oversized."""
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in sorted(files):
                if name.lower().endswith((".zip", ".fit")):
                    yield from _iter_fit_blobs(os.path.join(root, name))
        return
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".fit"):
                    continue
                label = f"{os.path.basename(path)}:{info.filename}"
                yield label, (zf.read(info) if info.file_size <= _MAX_BYTES else None)
        return
    if path.lower().endswith(".fit"):
        if os.path.getsize(path) > _MAX_BYTES:
            yield path, None
            return
        with open(path, "rb") as fh:
            yield path, fh.read()


def _start_local(start_utc: str) -> str:
    start = datetime.strptime(start_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return start.astimezone(athlete_tz()).strftime("%Y-%m-%dT%H:%M:%S")


def _record_avg_hr(records: list[dict]) -> float | None:
    """Time-weighted mean HR over the record stream (gaps capped like splits)."""
    total = weighted = 0.0
    for prev, cur in pairwise(records):
        dt = cur["t"] - prev["t"]
        if cur["hr"] and 0 < dt <= FIT_MAX_RECORD_GAP_S:
            total += dt
            weighted += dt * cur["hr"]
    return round(weighted / total, 1) if total else None


def _moving_secs(summary: dict) -> float:
    """
    Moving time comparable to Strava's: active lengths for pool swims (the
    timer keeps running through rest between sets), moving records for
    anything with a distance stream, else the timer (e.g. indoor rides).
    Never more than the timer: a multisport file's records span every leg.
    """
    timer = summary["timer_secs"]
    moving = summary.get("active_length_secs") or moving_seconds(summary["records"])
    return min(moving, timer) if moving else timer


def _summary_row(summary: dict, source: str) -> dict | None:
    """Trend summary row for a run / ride / swim, or None without a duration."""
    secs = round(_moving_secs(summary))
    if secs <= 0:
        return None
    return {
        "start_utc": summary["start_utc"],
        "start_local": _start_local(summary["start_utc"]),
        "sport": summary["sport"],
        "distance_m": summary["distance_m"],
        "moving_time": secs,
        "avg_hr": summary.get("avg_hr") or _record_avg_hr(summary["records"]),
        # Power is only meaningful on the bike; swim watches emit junk here.
        "avg_power": summary.get("avg_power") if summary["sport"] == "cycling" else None,
        "source": source,
    }


def _run_row(summary: dict, source: str) -> dict | None:
    """Store row for a decoded run, or None when it has no distance stream."""
    splits = splits_from_records(summary["records"])
    if not splits:
        return None
    return {
        "start_utc": summary["start_utc"],
        "start_local": _start_local(summary["start_utc"]),
        "sport": summary["sport"],
        "distance_m": summary["distance_m"] or sum(s["distance"] for s in splits),
        "moving_time": sum(s["moving_time"] for s in splits),
        "splits": splits,
        "source": source,
    }


def import_fit_activities(path: str, conn: sqlite3.Connection) -> dict:
    """
    Import every run, ride and swim found under `path`.

    Returns counts: files scanned, activities decoded, runs / rides / swims
    stored as trend summaries, runs stored with splits (`run_splits`), and
    files skipped as oversized or undecodable.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    stats = {
        "files": 0, "activities": 0, "runs": 0, "rides": 0, "swims": 0,
        "run_splits": 0, "oversized": 0, "errors": 0,
    }
    rows, summaries = [], {}
    for name, blob in _iter_fit_blobs(path):
        stats["files"] += 1
        if blob is None:
            stats["oversized"] += 1
            continue
        try:
            summary = fit_activity_summary(read_fit(blob))
        except FitError as exc:
            stats["errors"] += 1
            logger.debug("Skipping %s: %s", name, exc)
            continue
        if summary is None:
            continue
        stats["activities"] += 1
        if summary["sport"] not in TREND_SPORTS:
            continue
        source = os.path.basename(name.split(":")[-1])
        summary_row = _summary_row(summary, source)
        if summary_row:
            summaries[summary_row["start_utc"]] = summary_row  # same upload twice -> one row
        if summary["sport"] == "running" and (row := _run_row(summary, source)):
            rows.append(row)
    for row in summaries.values():
        stats[TREND_SPORTS[row["sport"]]] += 1
    upsert_fit_activities(conn, list(summaries.values()))
    stats["run_splits"] = upsert_fit_runs(conn, rows)
    return stats
