"""Backfill tests: pagination, resumption, and rate-limit handling. No network."""

import pytest

from tests.conftest import make_activity

from src.integrations import strava_backfill
from src.integrations.strava import StravaNetworkError, StravaRateLimitError
from src.storage.activity_store import count_activities, get_sync_state, set_sync_state


@pytest.fixture(autouse=True)
def no_page_delay(monkeypatch):
    """The inter-page pause is a courtesy to Strava, not behaviour under test."""
    monkeypatch.setattr(strava_backfill, "STRAVA_BACKFILL_PAGE_DELAY_SEC", 0)


def fake_pages(monkeypatch, pages: list[list[dict]]):
    """Serve `pages` by page number; anything beyond the list is an empty page."""
    requested = []

    def fetch(_token, per_page, page):
        requested.append(page)
        return pages[page - 1] if page - 1 < len(pages) else []

    monkeypatch.setattr(strava_backfill, "fetch_activities", fetch)
    return requested


def page_of(start_id: int, n: int) -> list[dict]:
    return [make_activity(start_id + i, "2026-08-03") for i in range(n)]


def test_walks_every_page_until_a_short_one(conn, monkeypatch):
    requested = fake_pages(monkeypatch, [page_of(1, 2), page_of(3, 2), page_of(5, 1)])
    result = strava_backfill.backfill_all("tok", conn, page_size=2)

    assert requested == [1, 2, 3]
    assert result["status"] == "complete"
    assert result["pages_fetched"] == 3
    assert count_activities(conn) == 5


def test_an_empty_first_page_terminates_immediately(conn, monkeypatch):
    fake_pages(monkeypatch, [])
    result = strava_backfill.backfill_all("tok", conn, page_size=2)

    assert result["status"] == "complete"
    assert result["total_activities"] == 0
    assert result["oldest"] is None


def test_a_full_final_page_still_terminates(conn, monkeypatch):
    """Strava signals the end with an empty page when the total divides evenly."""
    requested = fake_pages(monkeypatch, [page_of(1, 2), page_of(3, 2)])
    result = strava_backfill.backfill_all("tok", conn, page_size=2)

    assert requested == [1, 2, 3]
    assert result["status"] == "complete"
    assert count_activities(conn) == 4


def test_the_page_cap_stops_a_runaway_walk(conn, monkeypatch):
    fake_pages(monkeypatch, [page_of(i * 2 + 1, 2) for i in range(10)])
    result = strava_backfill.backfill_all("tok", conn, page_size=2, max_pages=3)

    assert result["status"] == "page_limit_reached"
    assert result["pages_fetched"] == 3
    assert result["next_page"] == 4


def test_a_rate_limit_stops_cleanly_with_the_cursor_on_the_failed_page(conn, monkeypatch):
    def fetch(_token, per_page, page):
        if page == 3:
            raise StravaRateLimitError("429: 201,1200")
        return page_of(page * 10, per_page)

    monkeypatch.setattr(strava_backfill, "fetch_activities", fetch)
    result = strava_backfill.backfill_all("tok", conn, page_size=2)

    assert result["status"] == "rate_limited"
    assert "201,1200" in result["error"]
    assert result["next_page"] == 3
    assert get_sync_state(conn, strava_backfill.CURSOR_KEY) == "3"
    # The two pages fetched before the limit are kept, not rolled back.
    assert count_activities(conn) == 4


def test_a_network_error_stops_cleanly(conn, monkeypatch):
    def fetch(_token, per_page, page):
        raise StravaNetworkError("Could not reach Strava")

    monkeypatch.setattr(strava_backfill, "fetch_activities", fetch)
    result = strava_backfill.backfill_all("tok", conn, page_size=2)

    assert result["status"] == "network_error"
    assert result["pages_fetched"] == 0


def test_an_interrupted_run_resumes_from_the_cursor(conn, monkeypatch):
    set_sync_state(conn, strava_backfill.CURSOR_KEY, 3)
    requested = fake_pages(monkeypatch, [page_of(1, 2), page_of(3, 2), page_of(5, 1)])

    strava_backfill.backfill_all("tok", conn, page_size=2)

    assert requested == [3]
    assert count_activities(conn) == 1


def test_resume_false_restarts_from_page_one(conn, monkeypatch):
    set_sync_state(conn, strava_backfill.CURSOR_KEY, 3)
    requested = fake_pages(monkeypatch, [page_of(1, 2), page_of(3, 1)])

    strava_backfill.backfill_all("tok", conn, page_size=2, resume=False)

    assert requested == [1, 2]
    assert count_activities(conn) == 3


def test_a_corrupt_cursor_falls_back_to_page_one(conn, monkeypatch):
    set_sync_state(conn, strava_backfill.CURSOR_KEY, "not-a-number")
    requested = fake_pages(monkeypatch, [page_of(1, 1)])

    strava_backfill.backfill_all("tok", conn, page_size=2)

    assert requested == [1]


def test_completion_is_recorded_only_on_a_clean_finish(conn, monkeypatch):
    def fetch(_token, per_page, page):
        raise StravaRateLimitError("429")

    monkeypatch.setattr(strava_backfill, "fetch_activities", fetch)
    strava_backfill.backfill_all("tok", conn, page_size=2)
    assert get_sync_state(conn, strava_backfill.COMPLETE_KEY) is None

    fake_pages(monkeypatch, [page_of(1, 1)])
    strava_backfill.backfill_all("tok", conn, page_size=2)
    assert get_sync_state(conn, strava_backfill.COMPLETE_KEY) == "1"


def test_rerunning_the_whole_backfill_is_idempotent(conn, monkeypatch):
    pages = [page_of(1, 2), page_of(3, 1)]
    fake_pages(monkeypatch, pages)

    strava_backfill.backfill_all("tok", conn, page_size=2, resume=False)
    strava_backfill.backfill_all("tok", conn, page_size=2, resume=False)

    assert count_activities(conn) == 3


def test_sync_recent_merges_without_touching_history(conn, monkeypatch):
    strava_backfill.upsert_activities(conn, [make_activity(1, "2024-01-01")])
    monkeypatch.setattr(
        strava_backfill, "fetch_activities", lambda _t, per_page, page: [make_activity(2, "2026-08-30")]
    )

    result = strava_backfill.sync_recent("tok", conn, count=50)

    assert result["fetched"] == 1
    assert result["total_activities"] == 2
