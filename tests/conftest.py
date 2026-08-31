"""Shared fixtures: synthetic activities and a throwaway history database."""

import pytest

from src.storage.activity_store import init_db


def make_activity(act_id: int, date: str, sport: str = "Run", **extra) -> dict:
    """A Strava summary with just the fields the store and analytics read."""
    act = {
        "id": act_id,
        "start_date_local": f"{date}T07:30:00Z",
        "sport_type": sport,
        "name": f"{sport} {act_id}",
        "distance": 10000.0,
        "moving_time": 3000,
        "total_elevation_gain": 50.0,
        "average_heartrate": 145.0,
    }
    act.update(extra)
    return act


@pytest.fixture
def activities() -> list[dict]:
    """Six activities across three sports and two weeks, newest last."""
    return [
        make_activity(1, "2026-08-03", "Run"),
        make_activity(2, "2026-08-05", "Ride"),
        make_activity(3, "2026-08-07", "Swim"),
        make_activity(4, "2026-08-10", "Run"),
        make_activity(5, "2026-08-12", "TrailRun"),
        make_activity(6, "2026-08-14", "Ride"),
    ]


@pytest.fixture
def conn(tmp_path):
    """A history DB in tmp_path — never the real `.training_history.db`."""
    connection = init_db(str(tmp_path / "history.db"))
    yield connection
    connection.close()
