"""Final Surge iCal plan source — offline, synthetic feeds only."""

import json
import time
from datetime import date

import pytest
import requests

from src import config
from src.integrations import final_surge, garmin_workouts, plan_source

FEED_URL = "webcal://log.finalsurge.com/delivery/ical/SECRETKEY123"

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Final Surge//EN
BEGIN:VEVENT
UID:w1
DTSTART;VALUE=DATE:20261118
SUMMARY:Run: Easy Run
DESCRIPTION:45 min easy @ 6:00/km\\nStrides x4
END:VEVENT
BEGIN:VEVENT
UID:w3
DTSTART:20261120T170000Z
SUMMARY:Swim
DESCRIPTION:2000m technique
END:VEVENT
BEGIN:VEVENT
UID:w2
DTSTART:20261120T050000Z
SUMMARY:Bike: Trainer Intervals
DESCRIPTION:Bike: Trainer Intervals 60 min with 5x5 min @ threshold
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def feed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FINAL_SURGE_ICAL_URL", FEED_URL)
    monkeypatch.setattr(config, "FINAL_SURGE_CACHE_FILE", str(tmp_path / "fs.json"))
    calls = []

    class Resp:
        text = ICS

        def raise_for_status(self):
            pass

    def fake_get(url, timeout):
        calls.append(url)
        return Resp()

    monkeypatch.setattr(final_surge.requests, "get", fake_get)
    return calls


def test_parse_groups_by_day_in_start_order():
    by_day = final_surge.parse_calendar(ICS)
    assert by_day[date(2026, 11, 18)][0]["text"] == "Run: Easy Run\n45 min easy @ 6:00/km\nStrides x4"
    fri = by_day[date(2026, 11, 20)]
    assert [w["uid"] for w in fri] == ["w2", "w3"]
    # Description already repeating the summary is not duplicated.
    assert fri[0]["text"].startswith("Bike: Trainer Intervals 60 min")


def test_webcal_rewritten_and_cached(feed):
    final_surge.fetch_calendar_text()
    final_surge.fetch_calendar_text()
    assert feed == ["https://log.finalsurge.com/delivery/ical/SECRETKEY123"]


def test_stale_cache_used_when_fetch_fails(feed, monkeypatch):
    with open(config.FINAL_SURGE_CACHE_FILE, "w") as f:
        json.dump({"fetched_at": time.time() - 10**6, "ics": ICS}, f)

    def boom(url, timeout):
        raise requests.ConnectionError(f"cannot reach {url}")

    monkeypatch.setattr(final_surge.requests, "get", boom)
    assert final_surge.fetch_calendar_text() == ICS


@pytest.mark.parametrize("leak", ["full", "path"])
def test_failure_reason_redacts_feed_url(feed, monkeypatch, leak):
    def boom(url, timeout):
        shown = url if leak == "full" else "/delivery/ical/SECRETKEY123"
        raise requests.ConnectionError(f"Max retries exceeded with url: {shown}")

    monkeypatch.setattr(final_surge.requests, "get", boom)
    res = final_surge.get_planned_workout_for_date(date(2026, 11, 18))
    assert res["found"] is False
    assert "SECRETKEY123" not in res["reason"]


def test_plan_source_dispatch(feed, monkeypatch):
    res = plan_source.get_planned_workout_for_date(date(2026, 11, 18))
    assert res["source"] == "final_surge" and res["found"]
    empty = plan_source.get_planned_workout_for_date(date(2026, 11, 19))
    assert empty["found"] is False

    monkeypatch.setattr(config, "FINAL_SURGE_ICAL_URL", "")
    import src.integrations.sheets as sheets_mod

    monkeypatch.setattr(sheets_mod, "get_planned_workout_for_date", lambda d: {"source": "sheet"})
    assert plan_source.get_planned_workout_for_date(date(2026, 11, 18)) == {"source": "sheet"}


def test_garmin_week_extraction_uses_final_surge(feed, monkeypatch):
    monkeypatch.setattr(garmin_workouts, "today_local", lambda: date(2026, 11, 17))
    week = garmin_workouts.extract_week_coach_workouts(0)
    assert week["week_start"] == "2026-11-16" and week["layout"] == "final_surge"
    days = {(d["date"], d["sport_type"]): d for d in week["days"]}
    # Unrecognised titles ("Run: Easy Run") fall back to the text classifier.
    assert days[("2026-11-18", "running")]["is_target"]
    assert days[("2026-11-20", "cycling")]["raw_text"].startswith("Bike: Trainer Intervals")
    assert days[("2026-11-20", "swim")]["is_target"] is False
    assert days[("2026-11-19", "rest")]["raw_text"] == ""


REAL_FORMAT_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a
DTSTART;VALUE=DATE:20261118
SUMMARY:Swim
DESCRIPTION:Workout: Swim\\n\\nPlanned: 2\\,000 m\\n\\n8x50 ελεύθερο τρέξιμο ποδιών
END:VEVENT
BEGIN:VEVENT
UID:b
DTSTART;VALUE=DATE:20261118
SUMMARY:Bike
DESCRIPTION:Workout: Bike\\n\\nPlanned: 1:00:00\\n\\n60' αερόβιο
END:VEVENT
BEGIN:VEVENT
UID:c
DTSTART;VALUE=DATE:20261117
SUMMARY:Run
DESCRIPTION:Workout: Run\\n\\nPlanned: 7.8 km\\n\\n12x400@21k(50'')
END:VEVENT
BEGIN:VEVENT
UID:d
DTSTART;VALUE=DATE:20261117
SUMMARY:Run
DESCRIPTION:Workout: Run
END:VEVENT
BEGIN:VEVENT
UID:e
DTSTART;VALUE=DATE:20261119
SUMMARY:Strength Training
DESCRIPTION:Workout: Strength Training
END:VEVENT
END:VCALENDAR
"""


def test_garmin_week_splits_double_days_by_title_sport(feed, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FINAL_SURGE_CACHE_FILE", str(tmp_path / "real.json"))

    class Resp:
        text = REAL_FORMAT_ICS

        def raise_for_status(self):
            pass

    monkeypatch.setattr(final_surge.requests, "get", lambda url, timeout: Resp())
    monkeypatch.setattr(garmin_workouts, "today_local", lambda: date(2026, 11, 17))
    days = garmin_workouts.extract_week_coach_workouts(0)["days"]
    by_key = {(d["date"], d["sport_type"]): d for d in days}

    # Swim + bike double day: the bike entry carries only bike text, swim is not pushed.
    bike = by_key[("2026-11-18", "cycling")]
    assert bike["is_target"] and "8x50" not in bike["raw_text"] and "60' αερόβιο" in bike["raw_text"]
    assert by_key[("2026-11-18", "swim")]["is_target"] is False

    # A bare "Workout: Run" placeholder is dropped; only the prescribed run remains.
    runs = [d for d in days if d["date"] == "2026-11-17"]
    assert len(runs) == 1 and "12x400" in runs[0]["raw_text"]

    # Days with only bare placeholders, or nothing at all, become rest.
    assert by_key[("2026-11-19", "rest")]["is_target"] is False
    assert by_key[("2026-11-22", "rest")]["raw_text"] == ""
