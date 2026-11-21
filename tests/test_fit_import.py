"""Importing runs, rides and swims from Garmin export FIT files into the history store."""

import io
import struct
import zipfile

import pytest

from src.integrations import fit_import
from src.integrations.fit_import import import_fit_activities
from src.storage.activity_store import get_fit_activities, get_fit_runs, upsert_fit_runs
from tests.fit_helpers import activity_fit


def _zip(path, members: dict[str, bytes]):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)
    path.write_bytes(buf.getvalue())
    return path


def test_zip_import_splits_runs_and_summarises_rides(tmp_path, conn):
    export = _zip(tmp_path / "UploadedFiles_0-_Part1.zip", {
        "run.fit": activity_fit(start_iso="2026-03-01T07:00:00+00:00", seconds=4200),
        "ride.fit": activity_fit(start_iso="2026-03-02T07:00:00+00:00", sport=2, seconds=600),
        "monitor.fit": activity_fit(seconds=5, file_type=32),
        "garbage.fit": b"definitely not FIT",
        "notes.txt": b"ignored",
    })
    stats = import_fit_activities(str(export), conn)
    assert stats == {
        "files": 4, "activities": 2, "runs": 1, "rides": 1, "swims": 0,
        "run_splits": 1, "oversized": 0, "errors": 1,
    }

    (run,) = get_fit_runs(conn)
    assert run["start_utc"] == "2026-03-01T07:00:00Z"
    assert run["start_local"] == "2026-03-01T09:00:00"  # Athens, EET in March
    assert run["sport"] == "running" and run["source"] == "run.fit"
    assert run["distance_m"] == 14700 and run["moving_time"] == 4200
    assert len(run["splits"]) == 15  # 14 full km + 700 m

    run_sum, ride = get_fit_activities(conn)  # oldest first
    assert run_sum["sport"] == "running" and run_sum["moving_time"] == 4200
    assert run_sum["avg_hr"] == 145  # no session HR -> record average
    assert ride["sport"] == "cycling" and ride["distance_m"] == 2100


def test_session_hr_and_power_are_kept_power_only_on_rides(tmp_path, conn):
    (tmp_path / "ride.fit").write_bytes(
        activity_fit(start_iso="2026-05-01T06:00:00+00:00", sport=2, seconds=600, session_hr=132, session_power=180)
    )
    (tmp_path / "swim.fit").write_bytes(
        activity_fit(start_iso="2026-05-02T06:00:00+00:00", sport=5, seconds=600, session_hr=120, session_power=999)
    )
    (tmp_path / "walk.fit").write_bytes(activity_fit(start_iso="2026-05-03T06:00:00+00:00", sport=11, seconds=600))
    stats = import_fit_activities(str(tmp_path), conn)
    assert (stats["rides"], stats["swims"], stats["runs"], stats["activities"]) == (1, 1, 0, 3)
    ride, swim = get_fit_activities(conn)
    assert (ride["avg_hr"], ride["avg_power"]) == (132, 180)
    assert (swim["avg_hr"], swim["avg_power"]) == (120, None)


def test_moving_time_drops_rest_like_strava(tmp_path, conn):
    # Outdoor ride with a 5-minute stop: moving time from the records.
    (tmp_path / "ride.fit").write_bytes(activity_fit(
        start_iso="2026-06-01T06:00:00+00:00", sport=2, seconds=1500, speed_ms=8.0, pause_after_s=600, pause_s=300,
    ))
    # Pool swim: 3 active lengths of 50 s, 2 rests of 60 s, timer 270 s.
    (tmp_path / "swim.fit").write_bytes(activity_fit(
        start_iso="2026-06-02T06:00:00+00:00", sport=5, seconds=270, speed_ms=0.0,
        lengths=[(50, 1), (60, 0), (50, 1), (60, 0), (50, 1)],
    ))
    # Indoor ride: no distance stream -> timer time.
    (tmp_path / "indoor.fit").write_bytes(activity_fit(start_iso="2026-06-03T06:00:00+00:00", sport=2, seconds=900, speed_ms=0.0))
    # Multisport file: records run past the first (swim) session's timer.
    multi = activity_fit(start_iso="2026-06-04T06:00:00+00:00", sport=5, seconds=900, speed_ms=1.0)
    (tmp_path / "multi.fit").write_bytes(multi.replace(struct.pack("<I", 900_000), struct.pack("<I", 600_000), 1))
    import_fit_activities(str(tmp_path), conn)
    ride, swim, indoor, leg = get_fit_activities(conn)
    assert leg["moving_time"] == 600
    assert ride["moving_time"] == 1200
    assert swim["moving_time"] == 150
    assert indoor["moving_time"] == 900


def test_directory_import_walks_fit_and_zip_files(tmp_path, conn):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "a.fit").write_bytes(activity_fit(start_iso="2026-04-01T06:00:00+00:00", seconds=900))
    _zip(tmp_path / "b.zip", {"b.fit": activity_fit(start_iso="2026-04-02T06:00:00+00:00", seconds=900)})
    stats = import_fit_activities(str(tmp_path), conn)
    assert stats["runs"] == 2 and stats["run_splits"] == 2
    assert [r["start_utc"] for r in get_fit_runs(conn)] == ["2026-04-02T06:00:00Z", "2026-04-01T06:00:00Z"]


def test_reimport_is_idempotent(tmp_path, conn):
    path = tmp_path / "a.fit"
    path.write_bytes(activity_fit(seconds=900))
    import_fit_activities(str(path), conn)
    import_fit_activities(str(path), conn)
    assert len(get_fit_runs(conn)) == 1 and len(get_fit_activities(conn)) == 1


def test_oversized_files_are_skipped(tmp_path, conn, monkeypatch):
    monkeypatch.setattr(fit_import, "_MAX_BYTES", 100)
    (tmp_path / "big.fit").write_bytes(activity_fit(seconds=60))
    _zip(tmp_path / "big.zip", {"big.fit": activity_fit(seconds=60)})
    stats = import_fit_activities(str(tmp_path), conn)
    assert stats["oversized"] == 2 and stats["runs"] == 0


def test_missing_path_raises(tmp_path, conn):
    with pytest.raises(FileNotFoundError):
        import_fit_activities(str(tmp_path / "nope"), conn)


def _row(start_utc, moving_time=4200):
    return {
        "start_utc": start_utc,
        "start_local": start_utc[:19],
        "sport": "running",
        "distance_m": 14000.0,
        "moving_time": moving_time,
        "splits": [{"distance": 1000.0, "moving_time": 300, "average_heartrate": 140}],
        "source": "x.fit",
    }


def test_store_round_trip_order_and_filter(conn):
    assert upsert_fit_runs(conn, [_row("2026-01-01T07:00:00Z"), _row("2026-02-01T07:00:00Z", 1200)]) == 2
    runs = get_fit_runs(conn)
    assert [r["start_utc"] for r in runs] == ["2026-02-01T07:00:00Z", "2026-01-01T07:00:00Z"]
    assert runs[1]["splits"][0]["average_heartrate"] == 140
    assert [r["start_utc"] for r in get_fit_runs(conn, min_moving_time=3600)] == ["2026-01-01T07:00:00Z"]
    assert upsert_fit_runs(conn, []) == 0
