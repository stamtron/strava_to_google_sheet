"""Multi-sport trends: Strava + Garmin export merge, period buckets, year over year."""

from datetime import date

import pytest

from src.analytics.sport_trends import merge_sessions, sport_trends

TODAY = date(2026, 11, 21)


def strava(act_id, day, sport="Run", secs=3600, dist=12000.0, hr=150.0, utc_time="06:00:00", **extra):
    act = {
        "id": act_id,
        "sport_type": sport,
        "start_date": f"{day}T{utc_time}Z",
        "start_date_local": f"{day}T08:00:00Z",
        "moving_time": secs,
        "distance": dist,
        "average_heartrate": hr,
        "has_heartrate": hr is not None,
    }
    act.update(extra)
    return act


def fit(day, sport="running", secs=3600, dist=12000.0, hr=150.0, power=None, utc_time="06:00:00"):
    return {
        "start_utc": f"{day}T{utc_time}Z",
        "start_local": f"{day}T08:00:00",
        "sport": sport,
        "distance_m": dist,
        "moving_time": secs,
        "avg_hr": hr,
        "avg_power": power,
        "source": "x.fit",
    }


def test_strava_wins_over_matching_fit_session_of_same_sport():
    sessions = merge_sessions(
        [strava(1, "2026-10-01")],
        [
            fit("2026-10-01", utc_time="06:02:00"),                     # same run, 2 min apart -> dropped
            fit("2026-10-01", sport="cycling", utc_time="06:01:00"),    # different sport -> kept
            fit("2026-10-01", utc_time="09:00:00"),                     # hours later -> kept
            fit("2026-10-01", sport="walking"),                         # not a trend sport
        ],
    )
    assert sorted((s["group"], s["source"]) for s in sessions) == [
        ("bike", "fit"), ("run", "fit"), ("run", "strava"),
    ]


def test_non_trend_sports_are_ignored():
    sessions = merge_sessions([strava(1, "2026-10-01", sport="WeightTraining")], [])
    assert sessions == []


def test_monthly_series_fills_gaps_and_computes_efficiency():
    result = sport_trends(
        [strava(1, "2026-08-10"), strava(2, "2026-10-05", secs=1800, dist=6000.0, hr=140.0)],
        [],
        TODAY,
    )
    assert result["periods"] == ["2026-08", "2026-09", "2026-10"]
    aug, sep, oct_ = result["sports"]["run"]["series"]
    assert aug["sessions"] == 1 and aug["hours"] == 1.0 and aug["distance_km"] == 12.0
    assert aug["efficiency"] == pytest.approx(200 / 150, abs=1e-3)  # 200 m/min at 150 bpm
    assert sep == {
        "period": "2026-09", "sessions": 0, "hours": 0.0, "distance_km": 0.0,
        "avg_hr": None, "avg_power": None, "efficiency": None, "fit_sessions": 0,
    }
    assert oct_["efficiency"] == pytest.approx(200 / 140, abs=1e-3)
    assert result["sports"]["bike"]["series"][0]["sessions"] == 0
    assert result["sources"] == {"strava": 2, "fit": 0}


def test_short_sessions_count_for_volume_but_not_efficiency():
    result = sport_trends([strava(1, "2026-11-01", secs=600, dist=2000.0)], [], TODAY)
    (nov,) = result["sports"]["run"]["series"]
    assert nov["sessions"] == 1 and nov["efficiency"] is None


def test_bike_efficiency_needs_device_power_and_hr():
    acts = [
        strava(1, "2026-11-01", sport="Ride", average_watts=180, device_watts=True, hr=120.0),
        strava(2, "2026-11-02", sport="VirtualRide", average_watts=500, device_watts=False, hr=120.0),
    ]
    fits = [fit("2026-11-03", sport="cycling", power=150, hr=None)]
    (nov,) = sport_trends(acts, fits, TODAY)["sports"]["bike"]["series"]
    assert nov["sessions"] == 3
    assert nov["avg_power"] == 165  # 180 W and 150 W, equal durations; estimated watts ignored
    assert nov["efficiency"] == pytest.approx(1.5)  # only ride 1 has both power and HR


def test_swim_efficiency_is_pace_per_100m():
    (nov,) = sport_trends([strava(1, "2026-11-01", sport="Swim", secs=2400, dist=2000.0)], [], TODAY)[
        "sports"
    ]["swim"]["series"]
    assert nov["efficiency"] == 120.0  # 2:00 / 100 m


def test_power_only_counts_on_the_bike():
    acts = [strava(1, "2026-11-01", sport="Swim", average_watts=3, device_watts=True),
            strava(2, "2026-11-02", average_watts=340, device_watts=True)]
    sports = sport_trends(acts, [], TODAY)["sports"]
    assert sports["swim"]["series"][0]["avg_power"] is None
    assert sports["run"]["series"][0]["avg_power"] is None


def test_quarter_buckets():
    result = sport_trends([strava(1, "2025-12-30"), strava(2, "2026-04-02")], [], TODAY, bucket="quarter")
    assert result["periods"] == ["2025-Q4", "2026-Q1", "2026-Q2"]
    with pytest.raises(ValueError):
        sport_trends([], [], TODAY, bucket="week")


def test_future_sessions_are_excluded_and_empty_input_is_safe():
    assert sport_trends([strava(1, "2026-12-01")], [], TODAY)["periods"] == []
    assert sport_trends([], [], TODAY) == {
        "bucket": "month", "periods": [], "sports": {}, "sources": {"strava": 0, "fit": 0},
    }


def test_year_over_year_status_and_direction():
    acts = [
        strava(1, "2026-10-01", dist=13200.0),               # 220 m/min now
        strava(2, "2025-10-01", dist=12000.0),               # 200 m/min a year ago
        strava(3, "2026-10-01", sport="Swim", secs=2400, dist=2000.0),  # 2:00/100 m now
    ]
    fits = [fit("2025-10-01", sport="swimming", secs=2200, dist=2000.0)]  # 1:50/100 m then
    sports = sport_trends(acts, fits, TODAY)["sports"]
    run = sports["run"]["year_over_year"]
    assert run["efficiency_change_pct"] == pytest.approx(10.0, abs=0.2)
    assert run["efficiency_status"] == "improved"
    assert run["hours_change_pct"] == 0.0
    swim = sports["swim"]["year_over_year"]
    assert swim["efficiency_status"] == "declined"  # slower pace is worse
    assert swim["previous"]["fit_sessions"] == 1
    assert sports["bike"]["year_over_year"]["efficiency_status"] == "insufficient"


def test_year_over_year_flat_band():
    acts = [strava(1, "2026-10-01", dist=12100.0), strava(2, "2025-10-01", dist=12000.0)]
    assert sport_trends(acts, [], TODAY)["sports"]["run"]["year_over_year"]["efficiency_status"] == "flat"
