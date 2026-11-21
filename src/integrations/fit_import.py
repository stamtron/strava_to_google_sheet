"""
Import runs from a Garmin Connect data export into the history store.

Garmin's GDPR export ships the original activity uploads as FIT files inside
`DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_*.zip`, alongside thousands
of daily monitoring files. This walks a directory, zip or single FIT file,
decodes each member in memory (nothing is extracted to disk), keeps only run
activities with distance, and stores them with per-km splits so the decoupling
trend can reach back past the Strava backfill.

The export is user-supplied and untrusted: members are size-capped, and a file
that fails to decode is counted and skipped rather than aborting the import.
"""

import logging
import os
import sqlite3
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime

from src.analytics.fit_splits import splits_from_records
from src.config import FIT_MAX_FILE_MB
from src.integrations.fit_file import FitError, fit_activity_summary, read_fit
from src.storage.activity_store import upsert_fit_runs
from src.timeutil import athlete_tz

logger = logging.getLogger(__name__)

_MAX_BYTES = int(FIT_MAX_FILE_MB * 1024 * 1024)


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


def _run_row(summary: dict, source: str) -> dict | None:
    """Store row for a decoded run, or None when it has no distance stream."""
    splits = splits_from_records(summary["records"])
    if not splits:
        return None
    start = datetime.strptime(summary["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return {
        "start_utc": summary["start_utc"],
        "start_local": start.astimezone(athlete_tz()).strftime("%Y-%m-%dT%H:%M:%S"),
        "sport": summary["sport"],
        "distance_m": summary["distance_m"] or sum(s["distance"] for s in splits),
        "moving_time": sum(s["moving_time"] for s in splits),
        "splits": splits,
        "source": source,
    }


def import_fit_runs(path: str, conn: sqlite3.Connection) -> dict:
    """
    Import every run activity found under `path` into `fit_runs`.

    Returns counts: files scanned, activities found, runs stored, files
    skipped as oversized or undecodable.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    stats = {"files": 0, "activities": 0, "runs": 0, "oversized": 0, "errors": 0}
    rows = []
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
        if summary["sport"] != "running":
            continue
        row = _run_row(summary, os.path.basename(name.split(":")[-1]))
        if row:
            rows.append(row)
    stats["runs"] = upsert_fit_runs(conn, rows)
    return stats
