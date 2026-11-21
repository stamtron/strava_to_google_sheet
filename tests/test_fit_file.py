"""FIT decoder and per-km split tests, on synthetic byte-built files."""

import pytest

from src.analytics.fit_splits import splits_from_records
from src.integrations.fit_file import FitError, fit_activity_summary, read_fit
from tests.fit_helpers import (
    FILE_ID,
    RECORD,
    SESSION,
    UINT8,
    UINT32,
    activity_fit,
    compressed,
    data,
    definition,
    fit_ts,
    wrap,
)


def test_activity_summary_is_scaled():
    summary = fit_activity_summary(read_fit(activity_fit(seconds=600, speed_ms=3.5, hr=150)))
    assert summary["start_utc"] == "2026-03-01T07:00:00Z"
    assert summary["sport"] == "running"
    assert summary["timer_secs"] == 600
    assert summary["distance_m"] == 2100
    assert len(summary["records"]) == 601
    last = summary["records"][-1]
    assert last["hr"] == 150 and last["dist"] == 2100 and last["speed"] == 3.5


def test_non_activity_file_stops_early():
    blob = activity_fit(seconds=10, file_type=32)  # monitoring
    fit = read_fit(blob)
    assert fit["file_type"] == 32 and fit["records"] == []
    assert fit_activity_summary(fit) is None
    assert len(read_fit(blob, stop_unless_activity=False)["records"]) == 11


def test_compressed_timestamps_roll_over():
    t0 = fit_ts("2026-03-01T07:00:00+00:00")
    t0 -= t0 % 32 - 8  # low 5 bits = 8
    short = [(3, UINT8), (5, UINT32)]
    body = definition(0, 0, FILE_ID) + data(0, FILE_ID, [4])
    body += definition(2, 20, RECORD) + data(2, RECORD, [t0, 140, 0, 3000])
    body += definition(3, 20, short)
    body += compressed(3, t0 + 2, short, [141, 600])   # offset 10 -> t0 + 2
    body += compressed(3, t0 + 26, short, [142, 1200])  # offset 2 < 10 -> rolls over to t0 + 26
    records = read_fit(wrap(body))["records"]
    assert [r["timestamp"] - t0 for r in records] == [0, 2, 26]
    assert records[2]["heart_rate"] == 142


def test_invalid_values_are_dropped_and_dev_fields_skipped():
    t0 = fit_ts("2026-03-01T07:00:00+00:00")
    body = definition(0, 0, FILE_ID) + data(0, FILE_ID, [4])
    body += definition(2, 20, RECORD, dev_sizes=[3])
    body += data(2, RECORD, [t0, 0xFF, 500, 3000], dev=b"\x01\x02\x03")
    body += data(2, RECORD, [t0 + 1, 150, 800, 3000], dev=b"\x01\x02\x03")
    body += definition(1, 18, SESSION) + data(1, SESSION, [t0, 1, 1000, 800])
    summary = fit_activity_summary(read_fit(wrap(body)))
    assert summary["records"][0]["hr"] is None
    assert summary["records"][1]["hr"] == 150 and summary["records"][1]["dist"] == 8.0


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"not a fit file at all",
        wrap(b"")[:-6] + b"\xff\xff\xff\xff",          # wrong signature / truncated
        wrap(definition(0, 0, FILE_ID))[:-3],              # data size beyond buffer
        wrap(data(0, FILE_ID, [4])),                       # data before any definition
        wrap(definition(0, 0, FILE_ID) + b"\x00"),         # data message cut short
    ],
)
def test_malformed_input_raises_fit_error(blob):
    with pytest.raises(FitError):
        read_fit(blob)


def test_activity_without_session_has_no_summary():
    body = definition(0, 0, FILE_ID) + data(0, FILE_ID, [4])
    assert fit_activity_summary(read_fit(wrap(body))) is None


def _records(seconds, speed=4.0, hr=150, start=0.0):
    return [{"t": start + i, "hr": hr, "dist": i * speed} for i in range(seconds + 1)]


def test_splits_cut_at_each_kilometre():
    splits = splits_from_records(_records(625))  # 2500 m at 4 m/s
    assert [s["distance"] for s in splits] == [1000.0, 1000.0, 500.0]
    assert [s["moving_time"] for s in splits] == [250, 250, 125]
    assert all(s["average_heartrate"] == 150 and s["average_speed"] == 4.0 for s in splits)


def test_pauses_count_as_elapsed_not_moving():
    first = _records(100)  # 400 m
    stopped = [{"t": 100 + i, "hr": 100, "dist": 400.0} for i in range(1, 21)]  # 20 s standing
    resumed = [{"t": 180 + i, "hr": 150, "dist": 400.0 + i * 4.0} for i in range(151)]  # after a 60 s gap
    (split,) = splits_from_records(first + stopped + resumed)[:1]
    assert split["moving_time"] == 250
    assert split["elapsed_time"] == 330
    assert split["average_heartrate"] == 150  # the standing HR of 100 isn't averaged in


def test_hr_is_time_weighted():
    recs = [{"t": i, "hr": 140 if i <= 125 else 160, "dist": i * 4.0} for i in range(251)]
    assert splits_from_records(recs)[0]["average_heartrate"] == 150.0


def test_records_without_distance_give_no_splits():
    assert splits_from_records([{"t": i, "hr": 150, "dist": None} for i in range(100)]) == []
    assert splits_from_records([]) == []
