"""Seeding the Garmin biometrics cache from a Garmin Connect data export."""

import json
from datetime import date

import pytest

import main
from src.integrations import garmin, garmin_export
from src.integrations.garmin import compute_biometric_baselines, summarize_health_days
from src.integrations.garmin_export import import_garmin_wellness, read_export_days
from src.storage.activity_store import get_fit_runs, init_db
from tests.fit_helpers import activity_fit

TODAY = date(2026, 11, 21)  # Saturday; current week starts 2026-11-16


@pytest.fixture
def garmin_cache(tmp_path, monkeypatch):
    path = tmp_path / "garmin_cache.json"
    monkeypatch.setattr(garmin, "GARMIN_CACHE_FILE", str(path))
    return path


def _uds(day, rhr=48, stress=25, charged=60, drained=55):
    return {
        "calendarDate": day,
        "restingHeartRate": rhr,
        "allDayStress": {"aggregatorList": [
            {"type": "ASLEEP", "averageStressLevel": 10},
            {"type": "TOTAL", "averageStressLevel": stress},
        ]},
        "bodyBattery": {"chargedValue": charged, "drainedValue": drained},
    }


def _sleep(day, hours=7.5, nap_min=0):
    third = hours * 3600 / 3
    item = {"calendarDate": day, "deepSleepSeconds": third, "lightSleepSeconds": third, "remSleepSeconds": third}
    if nap_min:
        item["napList"] = [{"napTimeSec": nap_min * 60}]
    return item


def _hrv(day, value=72):
    return {"calendarDate": day, "metrics": [{"type": "HR", "value": 50}, {"type": "HRV", "value": value}]}


def _export(root, uds, sleep, health):
    agg = root / "DI_CONNECT" / "DI-Connect-Aggregator"
    well = root / "DI_CONNECT" / "DI-Connect-Wellness"
    agg.mkdir(parents=True)
    well.mkdir(parents=True)
    (agg / "UDSFile_2026-11-01_2027-02-09.json").write_text(json.dumps(uds))
    (well / "123_sleepData.json").write_text(json.dumps(sleep))
    (well / "123_healthStatusData.json").write_text(json.dumps(health))
    return root


def _week(monday_day: int, month: int = 11, **kw):
    days = [f"2026-{month:02d}-{monday_day + i:02d}" for i in range(7)]
    return [_uds(d, **kw) for d in days], [_sleep(d) for d in days], [_hrv(d) for d in days]


def test_read_export_days_merges_sources(tmp_path):
    root = _export(tmp_path, [_uds("2026-11-02")], [_sleep("2026-11-02", nap_min=30)], [_hrv("2026-11-02")])
    days, stats = read_export_days(str(root))
    assert stats == {"files": 3, "skipped_files": 0, "days": 1}
    assert days["2026-11-02"] == {
        "rhr": 48.0, "stress": 25.0, "bb_charged": 60.0, "bb_drained": 55.0,
        "sleep_s": 27000.0, "nap_s": 1800.0, "hrv": 72.0,
    }


def test_malformed_and_oversized_files_are_skipped(tmp_path, monkeypatch):
    root = _export(tmp_path, {"not": "a list"}, [_sleep("2026-11-02"), "junk", {"calendarDate": 7}], [])
    (root / "DI_CONNECT" / "DI-Connect-Wellness" / "9_healthStatusData.json").write_text("{broken")
    days, stats = read_export_days(str(root))
    assert stats["files"] == 2 and stats["skipped_files"] == 2
    assert list(days) == ["2026-11-02"]

    monkeypatch.setattr(garmin_export, "_MAX_BYTES", 1)
    _days, stats = read_export_days(str(root))
    assert stats["files"] == 0 and stats["skipped_files"] == 4


def test_import_fills_cache_but_keeps_api_weeks_and_current_week(tmp_path, garmin_cache):
    w1 = _week(2)    # 2026-11-02: missing from cache -> written
    w2 = _week(9)    # 2026-11-09: complete API week -> kept
    w3 = _week(16)   # 2026-11-16: current week -> skipped
    uds, sleep, hrv = (a + b + c for a, b, c in zip(w1, w2, w3, strict=True))
    root = _export(tmp_path / "export", uds, sleep, hrv)
    api_week = {"summary": {"avg_rhr": 50, "avg_hrv": 70}, "fetched_at": 1.0}
    garmin_cache.write_text(json.dumps({
        "2026-11-09": api_week,
        "2026-10-26": {"summary": {"avg_rhr": 49}, "fetched_at": 1.0, "partial": True},
    }))

    stats = import_garmin_wellness(str(root), TODAY)
    assert (stats["weeks"], stats["weeks_written"], stats["weeks_kept"]) == (3, 1, 1)

    cache = json.loads(garmin_cache.read_text())
    assert cache["2026-11-09"] == api_week
    assert "2026-11-16" not in cache
    written = cache["2026-11-02"]
    assert written["source"] == "garmin_export"
    assert written["summary"]["avg_rhr"] == 48 and written["summary"]["avg_hrv"] == 72
    assert written["summary"]["avg_sleep_h"] == pytest.approx(7.5)
    assert written["summary"]["avg_readiness"] is None  # not in the export


def test_import_replaces_partial_cache_weeks(tmp_path, garmin_cache):
    root = _export(tmp_path / "export", *_week(2, rhr=46))
    garmin_cache.write_text(json.dumps({"2026-11-02": {"summary": {"avg_rhr": 52}, "fetched_at": 1.0, "partial": True}}))
    assert import_garmin_wellness(str(root), TODAY)["weeks_written"] == 1
    assert json.loads(garmin_cache.read_text())["2026-11-02"]["summary"]["avg_rhr"] == 46


def test_import_requires_a_directory(tmp_path, garmin_cache):
    with pytest.raises(FileNotFoundError):
        import_garmin_wellness(str(tmp_path / "missing"), TODAY)


def test_summarize_health_days_rules():
    summary = summarize_health_days([
        {"rhr": 0, "hrv": 70, "bb_charged": 0, "bb_drained": 40, "sleep_s": 7 * 3600},
        {"rhr": 50, "hrv": 74, "bb_charged": 50, "bb_drained": 60, "nap_s": 3600},
    ])
    assert summary["avg_rhr"] == 50          # 0 means "not measured"
    assert summary["avg_bb_charged"] == 25   # 0 charge is a real reading
    assert summary["total_sleep_h"] == 8.0 and summary["avg_sleep_h"] == 4.0
    assert summary["total_nap_h"] == 1.0
    assert summarize_health_days([]) is None
    assert summarize_health_days([{"rhr": 0, "readiness": 60}]) is None


def test_baselines_use_only_recent_window():
    weeks = {f"2026-{m:02d}-01": {"avg_rhr": 60, "avg_hrv": 50} for m in range(1, 7)}
    weeks.update({f"2026-{m:02d}-01": {"avg_rhr": 48, "avg_hrv": 72} for m in range(7, 11)})
    recent = compute_biometric_baselines(weeks, window_weeks=4)
    assert recent["rhr"]["weeks_used"] == 4 and recent["rhr"]["baseline_mean"] == 48.0
    assert recent["hrv"]["baseline_mean"] == 72.0
    assert compute_biometric_baselines(weeks, window_weeks=0)["rhr"]["weeks_used"] == 10


def test_cli_imports_runs_and_wellness(tmp_path, garmin_cache, monkeypatch, capsys):
    root = _export(tmp_path / "export", *_week(2))
    uploads = root / "DI_CONNECT" / "DI-Connect-Uploaded-Files"
    uploads.mkdir()
    (uploads / "run.fit").write_bytes(activity_fit(seconds=4200))
    db = tmp_path / "history.db"
    monkeypatch.setattr(main, "init_db", lambda: init_db(str(db)))
    monkeypatch.setattr(main, "today_local", lambda: TODAY)

    assert main.run_garmin_export_import(str(root)) == 0
    out = capsys.readouterr().out
    assert "1 runs (1 with splits), 0 rides, 0 swims stored" in out and "1 written" in out
    conn = init_db(str(db))
    assert len(get_fit_runs(conn)) == 1
    conn.close()
    assert "2026-11-02" in json.loads(garmin_cache.read_text())

    assert main.run_garmin_export_import(str(tmp_path / "nope")) == 1
