"""
Paginated import of the complete Strava activity history into the local store.

`fetch_activities` has always accepted a `page` argument but nothing ever
incremented it, so the app only ever saw the most recent page. These helpers
walk every page once, persist as they go, and record a cursor so a run
interrupted by a rate limit resumes where it stopped instead of starting over.

Throttling and retries are handled inside `strava._request` (429 with
`Retry-After`); nothing here re-implements them.
"""

import time

from src.config import (
    STRAVA_BACKFILL_MAX_PAGES,
    STRAVA_BACKFILL_PAGE_DELAY_SEC,
    STRAVA_BACKFILL_PAGE_SIZE,
)
from src.integrations.strava import (
    StravaNetworkError,
    StravaRateLimitError,
    fetch_activities,
)
from src.storage.activity_store import (
    count_activities,
    date_range,
    get_sync_state,
    set_sync_state,
    upsert_activities,
)

# sync_state keys
CURSOR_KEY = "backfill_next_page"
COMPLETE_KEY = "backfill_complete"


def backfill_all(
    access_token: str,
    conn,
    page_size: int = STRAVA_BACKFILL_PAGE_SIZE,
    max_pages: int = STRAVA_BACKFILL_MAX_PAGES,
    resume: bool = True,
    progress: bool = False,
) -> dict:
    """
    Walk Strava's activity list from the current cursor to the end, storing
    every page.

    Stops on a short or empty page — Strava returns fewer than `per_page`
    results only on the final page. A rate limit or network error ends the run
    cleanly with the cursor pointing at the page that failed, so re-running
    picks up from there.

    Set `resume=False` to restart the walk from page 1; already-stored
    activities are upserted, so a full re-run is idempotent, just slower.
    """
    start_page = 1
    if resume:
        saved = get_sync_state(conn, CURSOR_KEY)
        if saved:
            try:
                start_page = max(1, int(saved))
            except ValueError:
                start_page = 1

    pages_fetched = 0
    stored = 0
    page = start_page
    status = "complete"
    error = None

    while pages_fetched < max_pages:
        try:
            batch = fetch_activities(access_token, per_page=page_size, page=page)
        except StravaRateLimitError as e:
            status, error = "rate_limited", str(e)
            break
        except StravaNetworkError as e:
            status, error = "network_error", str(e)
            break

        pages_fetched += 1
        stored += upsert_activities(conn, batch)
        if progress:
            print(f"  page {page}: {len(batch)} activities")

        # Advance the cursor only after the page is committed, so a crash
        # between the fetch and the write re-fetches rather than skips.
        page += 1
        set_sync_state(conn, CURSOR_KEY, page)

        if len(batch) < page_size:
            break

        if STRAVA_BACKFILL_PAGE_DELAY_SEC > 0:
            time.sleep(STRAVA_BACKFILL_PAGE_DELAY_SEC)
    else:
        status = "page_limit_reached"

    if status == "complete":
        set_sync_state(conn, COMPLETE_KEY, "1")

    span = date_range(conn)
    result = {
        "status": status,
        "pages_fetched": pages_fetched,
        "activities_stored": stored,
        "total_activities": count_activities(conn),
        "next_page": page,
        "oldest": span[0].isoformat() if span else None,
        "newest": span[1].isoformat() if span else None,
    }
    if error:
        result["error"] = error
    return result


def sync_recent(access_token: str, conn, count: int = 50) -> dict:
    """
    Fetch only the most recent page and merge it into the store.

    This is the cheap path a normal dashboard load takes: new activities appear,
    edits to recent ones are picked up, and the historical rows are untouched.
    """
    batch = fetch_activities(access_token, per_page=count, page=1)
    stored = upsert_activities(conn, batch)
    return {
        "status": "ok",
        "fetched": len(batch),
        "activities_stored": stored,
        "total_activities": count_activities(conn),
    }
