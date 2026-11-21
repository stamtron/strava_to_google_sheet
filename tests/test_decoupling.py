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
