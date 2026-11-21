from src.analytics.decoupling import classify_decoupling, decoupling_trend, run_decoupling


def _splits(n, pace_s=300, hr_start=140.0, hr_step=0.0, pace_step=0.0):
    """n one-km splits; HR rises by hr_step per km, pace slows by pace_step s/km."""
    out = []
    for i in range(n):
        secs = pace_s + pace_step * i
        out.append({"distance": 1000.0, "moving_time": secs, "average_heartrate": hr_start + hr_step * i})
    return out


def test_steady_run_with_flat_hr_is_coupled():
    res = run_decoupling(_splits(14))
    assert res["decoupling_pct"] == 0.0
    assert res["status"] == "good"
    assert res["duration_min"] == 70.0


def test_hr_drift_at_same_pace_is_decoupling():
    res = run_decoupling(_splits(14, hr_step=1.5))
    # First-half mean HR 144.5, second-half 155: EF drops ~6.8%.
    assert 6.0 < res["decoupling_pct"] < 7.5
    assert res["status"] == "moderate"


def test_short_unsteady_or_hr_less_runs_do_not_qualify():
    assert run_decoupling(_splits(8)) is None  # 40 min
    intervals = []
    for i in range(14):
        intervals.append({"distance": 1000.0, "moving_time": 230 if i % 2 else 380, "average_heartrate": 150})
    assert run_decoupling(intervals) is None
    no_hr = [{**s, "average_heartrate": None} for s in _splits(14)]
    assert run_decoupling(no_hr) is None


def test_partial_final_split_is_ignored():
    splits = [*_splits(14), {"distance": 200.0, "moving_time": 50, "average_heartrate": 190}]
    assert run_decoupling(splits)["decoupling_pct"] == 0.0


def test_classify_thresholds():
    assert classify_decoupling(None) == "unknown"
    assert classify_decoupling(4.9) == "good"
    assert classify_decoupling(5.0) == "moderate"
    assert classify_decoupling(10.0) == "high"


def test_trend_orders_runs_and_counts_missing_detail():
    acts = [
        {"id": 2, "sport_type": "Run", "moving_time": 4200, "distance": 14000, "start_date_local": "2026-11-15T08:00:00"},
        {"id": 1, "sport_type": "Run", "moving_time": 4200, "distance": 14000, "start_date_local": "2026-11-08T08:00:00"},
        {"id": 3, "sport_type": "Run", "moving_time": 4200, "distance": 14000, "start_date_local": "2026-11-18T08:00:00"},
        {"id": 4, "sport_type": "Ride", "moving_time": 7200, "distance": 60000, "start_date_local": "2026-11-16T08:00:00"},
        {"id": 5, "sport_type": "Run", "moving_time": 1800, "distance": 6000, "start_date_local": "2026-11-17T08:00:00"},
    ]
    details = {"1": {"splits_metric": _splits(14, hr_step=2.5)}, 2: {"splits_metric": _splits(14)}}
    trend = decoupling_trend(acts, details)
    assert [r["id"] for r in trend["runs"]] == [1, 2]
    assert trend["runs"][0]["status"] == "high"
    assert trend["latest_pct"] == 0.0
    assert trend["runs_without_detail"] == 1  # run 3; the ride and short run aren't candidates


def test_trend_empty():
    trend = decoupling_trend([], {})
    assert trend["count"] == 0 and trend["latest_pct"] is None and trend["recent_status"] == "unknown"


def _fit_run(start_utc, start_local, splits, moving_time=4200):
    return {
        "start_utc": start_utc,
        "start_local": start_local,
        "distance_m": 14000.0,
        "moving_time": moving_time,
        "splits": splits,
    }


def _run(act_id, start_utc, start_local):
    return {
        "id": act_id, "sport_type": "Run", "name": f"Run {act_id}", "moving_time": 4200,
        "distance": 14000, "start_date": start_utc, "start_date_local": start_local,
    }


def test_fit_splits_fill_in_missing_strava_detail():
    acts = [_run(1, "2026-11-08T06:00:00Z", "2026-11-08T08:00:00")]
    fit = [_fit_run("2026-11-08T06:01:30Z", "2026-11-08T08:01:30", _splits(14, hr_step=2.5))]
    trend = decoupling_trend(acts, {}, fit_runs=fit)
    assert trend["runs_without_detail"] == 0
    (run,) = trend["runs"]
    assert run["id"] == 1 and run["source"] == "fit" and run["status"] == "high"
    assert trend["fit_runs"] == 1


def test_strava_detail_wins_and_matched_fit_run_is_not_duplicated():
    acts = [_run(1, "2026-11-08T06:00:00Z", "2026-11-08T08:00:00")]
    fit = [_fit_run("2026-11-08T06:00:00Z", "2026-11-08T08:00:00", _splits(14, hr_step=2.5))]
    trend = decoupling_trend(acts, {"1": {"splits_metric": _splits(14)}}, fit_runs=fit)
    (run,) = trend["runs"]
    assert run["source"] == "strava" and run["decoupling_pct"] == 0.0
    assert trend["fit_runs"] == 0


def test_unmatched_fit_runs_extend_history():
    acts = [_run(1, "2026-11-08T06:00:00Z", "2026-11-08T08:00:00")]
    fit = [
        _fit_run("2026-11-08T06:30:00Z", "2026-11-08T08:30:00", _splits(14)),  # outside match tolerance
        _fit_run("2026-03-01T07:00:00Z", "2026-03-01T09:00:00", _splits(14)),
        _fit_run("2026-03-05T07:00:00Z", "2026-03-05T09:00:00", _splits(14), moving_time=1800),  # too short
    ]
    trend = decoupling_trend(acts, {"1": {"splits_metric": _splits(14)}}, fit_runs=fit)
    assert [(r["date"], r["id"], r["source"]) for r in trend["runs"]] == [
        ("2026-03-01", None, "fit"),
        ("2026-11-08", 1, "strava"),
        ("2026-11-08", None, "fit"),
    ]
    assert trend["runs"][0]["name"] is None
    assert trend["fit_runs"] == 2


def test_fit_runs_respect_the_limit():
    fit = [
        _fit_run(f"2026-0{m}-01T07:00:00Z", f"2026-0{m}-01T09:00:00", _splits(14)) for m in range(1, 6)
    ]
    trend = decoupling_trend([], {}, limit=2, fit_runs=fit)
    assert [r["date"] for r in trend["runs"]] == ["2026-04-01", "2026-05-01"]
