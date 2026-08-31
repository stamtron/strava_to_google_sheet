"""
SQLite-backed store for the full Strava activity history.

The dashboard's JSON cache only ever holds the last page Strava returned, so
history older than that page is lost on every refresh. This store keeps
everything that has ever been fetched, which is what the load, durability, and
coaching features need in order to look further back than a few weeks.

Each activity is stored with its full summary JSON in `payload` rather than
spread across a column per field. Strava adds and renames summary keys, and a
wide table would need a migration every time the app starts reading a new one.
The two columns that are actually filtered on — date and sport — are lifted out
and indexed.
"""

import json
import sqlite3
import time
from datetime import date, datetime, timedelta

from src.config import HISTORY_DB_FILE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id                INTEGER PRIMARY KEY,
    start_date_local  TEXT NOT NULL,
    sport_type        TEXT NOT NULL,
    payload           TEXT NOT NULL,
    detail            TEXT,
    fetched_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_date
    ON activities (start_date_local);
CREATE INDEX IF NOT EXISTS idx_activities_sport_date
    ON activities (sport_type, start_date_local);

CREATE TABLE IF NOT EXISTS sync_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def init_db(path: str | None = None) -> sqlite3.Connection:
    """
    Open (creating if needed) the history database and return a connection.

    Idempotent: safe to call on every request. The caller owns the connection
    and should close it, or reuse one per thread — sqlite3 connections are not
    shareable across threads by default.
    """
    conn = sqlite3.connect(path or HISTORY_DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _sport_of(act: dict) -> str:
    return act.get("sport_type") or act.get("type") or "Unknown"


def upsert_activities(conn: sqlite3.Connection, activities: list[dict]) -> int:
    """
    Insert or refresh activity summaries. Returns the number of rows written.

    An existing row keeps its `detail` — details are fetched separately and are
    far more expensive than summaries, so a re-sync of the summary list must not
    discard them.
    """
    rows = []
    now = time.time()
    for act in activities:
        act_id = act.get("id")
        start = act.get("start_date_local")
        if act_id is None or not start:
            continue
        rows.append((int(act_id), start, _sport_of(act), json.dumps(act), now))

    if not rows:
        return 0

    conn.executemany(
        """
        INSERT INTO activities (id, start_date_local, sport_type, payload, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            start_date_local = excluded.start_date_local,
            sport_type       = excluded.sport_type,
            payload          = excluded.payload,
            fetched_at       = excluded.fetched_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_details(conn: sqlite3.Connection, details: dict) -> int:
    """
    Attach detail payloads to already-stored activities, keyed by activity id
    (str or int, as `fetch_details_for_activities` returns them).

    Details for an activity that is not in the store yet are skipped rather than
    inserted, because a detail alone lacks nothing but is not a summary and
    would produce a row the rest of the app cannot read.
    """
    rows = []
    now = time.time()
    for act_id, detail in (details or {}).items():
        try:
            key = int(act_id)
        except (TypeError, ValueError):
            continue
        rows.append((json.dumps(detail), now, key))

    if not rows:
        return 0

    cur = conn.executemany(
        "UPDATE activities SET detail = ?, fetched_at = ? WHERE id = ?",
        rows,
    )
    conn.commit()
    return cur.rowcount


def get_activities(
    conn: sqlite3.Connection,
    since: date | None = None,
    until: date | None = None,
    sports: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    Return stored activity summaries, newest first.

    `since` and `until` are inclusive calendar bounds on the local start date.
    Comparison is lexicographic on the ISO timestamp, which is ordered the same
    way as chronological for ISO-8601 strings; `until` is bumped by a day so a
    same-day activity with a time component is not excluded.
    """
    clauses, params = [], []

    if since is not None:
        clauses.append("start_date_local >= ?")
        params.append(since.isoformat())
    if until is not None:
        clauses.append("start_date_local < ?")
        params.append((until + timedelta(days=1)).isoformat())
    if sports:
        clauses.append(f"sport_type IN ({','.join('?' * len(sports))})")
        params.extend(sports)

    sql = "SELECT payload FROM activities"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY start_date_local DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    return [json.loads(r["payload"]) for r in conn.execute(sql, params)]


def get_details(conn: sqlite3.Connection, activity_ids: list | None = None) -> dict:
    """
    Return stored detail payloads keyed by stringified activity id, matching the
    shape `fetch_details_for_activities` produces so the two are interchangeable.
    """
    sql = "SELECT id, detail FROM activities WHERE detail IS NOT NULL"
    params: list = []
    if activity_ids:
        ids = [int(a) for a in activity_ids]
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params = ids
    return {str(r["id"]): json.loads(r["detail"]) for r in conn.execute(sql, params)}


def count_activities(conn: sqlite3.Connection) -> int:
    """Total number of stored activities."""
    return conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]


def date_range(conn: sqlite3.Connection) -> tuple[date, date] | None:
    """Oldest and newest stored activity date, or None when the store is empty."""
    row = conn.execute(
        "SELECT MIN(start_date_local) AS lo, MAX(start_date_local) AS hi FROM activities"
    ).fetchone()
    if not row or not row["lo"]:
        return None
    return _to_date(row["lo"]), _to_date(row["hi"])


def _to_date(raw: str) -> date:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()


def get_sync_state(conn: sqlite3.Connection, key: str) -> str | None:
    """Read a persisted sync watermark, or None if it was never set."""
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_sync_state(conn: sqlite3.Connection, key: str, value) -> None:
    """Persist a sync watermark. Values are stored as text."""
    conn.execute(
        """
        INSERT INTO sync_state (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
    conn.commit()
