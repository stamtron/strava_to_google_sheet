"""Importing runs from Garmin export FIT files into the history store."""

import io
import zipfile

import pytest

from src.integrations import fit_import
from src.integrations.fit_import import import_fit_runs
from src.storage.activity_store import get_fit_runs, upsert_fit_runs
from tests.fit_helpers import activity_fit


def _zip(path, members: dict[str, bytes]):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)
    path.write_bytes(buf.getvalue())
    return path


def test_zip_import_keeps_only_runs(tmp_path, conn):
    export = _zip(tmp_path / "UploadedFiles_0-_Part1.zip", {
        "run.fit": activity_fit(start_iso="2026-03-01T07:00:00+00:00", seconds=4200),
        "ride.fit": activity_fit(start_iso="2026-03-02T07:00:00+00:00", sport=2, seconds=600),
        "monitor.fit": activity_fit(seconds=5, file_type=32),
        "garbage.fit": b"definitely not FIT",
        "notes.txt": b"ignored",
    })
    stats = import_fit_runs(str(export), conn)
    assert stats == {"files": 4, "activities": 2, "runs": 1, "oversized": 0, "errors": 1}

    (run,) = get_fit_runs(conn)
    assert run["start_utc"] == "2026-03-01T07:00:00Z"
    assert run["start_local"] == "2026-03-01T09:00:00"  # Athens, EET in March
    assert run["sport"] == "running" and run["source"] == "run.fit"
    assert run["distance_m"] == 14700 and run["moving_time"] == 4200
    assert len(run["splits"]) == 15  # 14 full km + 700 m


def test_directory_import_walks_fit_and_zip_files(tmp_path, conn):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "a.fit").write_bytes(activity_fit(start_iso="2026-04-01T06:00:00+00:00", seconds=900))
    _zip(tmp_path / "b.zip", {"b.fit": activity_fit(start_iso="2026-04-02T06:00:00+00:00", seconds=900)})
    stats = import_fit_runs(str(tmp_path), conn)
    assert stats["runs"] == 2
    assert [r["start_utc"] for r in get_fit_runs(conn)] == ["2026-04-02T06:00:00Z", "2026-04-01T06:00:00Z"]


def test_reimport_is_idempotent(tmp_path, conn):
    path = tmp_path / "a.fit"
    path.write_bytes(activity_fit(seconds=900))
    import_fit_runs(str(path), conn)
    import_fit_runs(str(path), conn)
    assert len(get_fit_runs(conn)) == 1


def test_oversized_files_are_skipped(tmp_path, conn, monkeypatch):
    monkeypatch.setattr(fit_import, "_MAX_BYTES", 100)
    (tmp_path / "big.fit").write_bytes(activity_fit(seconds=60))
    _zip(tmp_path / "big.zip", {"big.fit": activity_fit(seconds=60)})
    stats = import_fit_runs(str(tmp_path), conn)
    assert stats["oversized"] == 2 and stats["runs"] == 0


def test_missing_path_raises(tmp_path, conn):
    with pytest.raises(FileNotFoundError):
        import_fit_runs(str(tmp_path / "nope"), conn)


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
