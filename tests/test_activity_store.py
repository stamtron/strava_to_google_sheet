"""Persistence tests for the SQLite history store, against a tmp_path database."""

from datetime import date

from tests.conftest import make_activity

from src.storage.activity_store import (
    count_activities,
    date_range,
    get_activities,
    get_details,
    get_sync_state,
    set_sync_state,
    upsert_activities,
    upsert_details,
)


def test_roundtrip_preserves_the_full_summary(conn, activities):
    """The payload is stored whole so unknown Strava fields survive."""
    upsert_activities(conn, [make_activity(1, "2026-08-03", "Run", some_future_key="x")])
    stored = get_activities(conn)
    assert stored[0]["some_future_key"] == "x"


def test_upsert_is_idempotent(conn, activities):
    upsert_activities(conn, activities)
    upsert_activities(conn, activities)
    assert count_activities(conn) == len(activities)


def test_upsert_refreshes_an_edited_activity(conn):
    upsert_activities(conn, [make_activity(1, "2026-08-03", "Run", name="Morning Run")])
    upsert_activities(conn, [make_activity(1, "2026-08-03", "Run", name="Renamed")])
    assert count_activities(conn) == 1
    assert get_activities(conn)[0]["name"] == "Renamed"


def test_activities_without_an_id_or_date_are_skipped(conn):
    written = upsert_activities(conn, [{"id": 1}, {"start_date_local": "2026-08-03T07:00:00Z"}])
    assert written == 0
    assert count_activities(conn) == 0


def test_results_are_newest_first(conn, activities):
    upsert_activities(conn, activities)
    ids = [a["id"] for a in get_activities(conn)]
    assert ids == [6, 5, 4, 3, 2, 1]


def test_limit_takes_the_newest(conn, activities):
    upsert_activities(conn, activities)
    assert [a["id"] for a in get_activities(conn, limit=2)] == [6, 5]


def test_date_bounds_are_inclusive_despite_a_time_component(conn, activities):
    """`until` must not exclude an activity that happened later that same day."""
    upsert_activities(conn, activities)
    got = get_activities(conn, since=date(2026, 8, 5), until=date(2026, 8, 10))
    assert [a["id"] for a in got] == [4, 3, 2]


def test_sport_filter_accepts_a_group(conn, activities):
    upsert_activities(conn, activities)
    runs = get_activities(conn, sports=("Run", "TrailRun"))
    assert [a["id"] for a in runs] == [5, 4, 1]


def test_filters_combine(conn, activities):
    upsert_activities(conn, activities)
    got = get_activities(conn, since=date(2026, 8, 8), sports=("Run", "TrailRun"))
    assert [a["id"] for a in got] == [5, 4]


def test_details_are_keyed_by_string_id_like_the_strava_client(conn, activities):
    upsert_activities(conn, activities)
    upsert_details(conn, {"1": {"suffer_score": 80}})
    assert get_details(conn) == {"1": {"suffer_score": 80}}


def test_details_survive_a_summary_resync(conn, activities):
    """Details cost one rate-limited call each; a summary refresh must keep them."""
    upsert_activities(conn, activities)
    upsert_details(conn, {1: {"suffer_score": 80}})
    upsert_activities(conn, activities)
    assert get_details(conn)["1"] == {"suffer_score": 80}


def test_details_for_unknown_activities_are_dropped(conn):
    assert upsert_details(conn, {"999": {"suffer_score": 1}}) == 0
    assert count_activities(conn) == 0


def test_details_can_be_restricted_to_given_ids(conn, activities):
    upsert_activities(conn, activities)
    upsert_details(conn, {1: {"suffer_score": 80}, 2: {"suffer_score": 90}})
    assert list(get_details(conn, [2])) == ["2"]


def test_date_range_is_none_when_empty(conn):
    assert date_range(conn) is None


def test_date_range_spans_oldest_to_newest(conn, activities):
    upsert_activities(conn, activities)
    assert date_range(conn) == (date(2026, 8, 3), date(2026, 8, 14))


def test_sync_state_is_absent_until_written(conn):
    assert get_sync_state(conn, "backfill_next_page") is None


def test_sync_state_overwrites_and_stringifies(conn):
    set_sync_state(conn, "backfill_next_page", 3)
    set_sync_state(conn, "backfill_next_page", 7)
    assert get_sync_state(conn, "backfill_next_page") == "7"


def test_init_db_is_safe_to_call_repeatedly(tmp_path, activities):
    from src.storage.activity_store import init_db

    path = str(tmp_path / "again.db")
    first = init_db(path)
    upsert_activities(first, activities)
    first.close()

    second = init_db(path)
    assert count_activities(second) == len(activities)
    second.close()
