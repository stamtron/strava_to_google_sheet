"""
Per-kilometre splits from a FIT record stream, in Strava's `splits_metric` shape.

Lets runs imported from a Garmin export feed the same decoupling maths as runs
with a Strava detail payload. Heart rate is time-weighted over moving records
only, so a stop at a traffic light doesn't drag a split's average down.
"""

from itertools import pairwise

from src.config import FIT_MAX_RECORD_GAP_S, FIT_MOVING_SPEED_MS, FIT_SPLIT_METERS


def _empty() -> dict:
    return {"distance": 0.0, "moving_time": 0.0, "elapsed_time": 0.0, "hr_sum": 0.0, "hr_secs": 0.0}


def _close(split: dict) -> dict:
    moving = split["moving_time"]
    return {
        "distance": round(split["distance"], 1),
        "moving_time": round(moving),
        "elapsed_time": round(split["elapsed_time"]),
        "average_speed": round(split["distance"] / moving, 3) if moving else None,
        "average_heartrate": round(split["hr_sum"] / split["hr_secs"], 1) if split["hr_secs"] else None,
    }


def splits_from_records(records: list[dict], split_m: float = FIT_SPLIT_METERS) -> list[dict]:
    """
    Cut records ({"t", "hr", "dist"}; `dist` cumulative metres) into splits of
    `split_m`. The final partial split is kept; consumers drop short tails.
    Records without distance (e.g. a treadmill run with no footpod) are ignored.
    """
    stream = sorted((r for r in records if r.get("dist") is not None), key=lambda r: r["t"])
    splits = []
    split = _empty()
    boundary = split_m
    for prev, cur in pairwise(stream):
        dt = cur["t"] - prev["t"]
        dd = max(cur["dist"] - prev["dist"], 0.0)
        if dt <= 0:
            continue
        split["elapsed_time"] += dt
        split["distance"] += dd
        if dt <= FIT_MAX_RECORD_GAP_S and dd / dt >= FIT_MOVING_SPEED_MS:
            split["moving_time"] += dt
            if cur.get("hr"):
                split["hr_sum"] += cur["hr"] * dt
                split["hr_secs"] += dt
        if cur["dist"] >= boundary:
            splits.append(_close(split))
            split = _empty()
            while boundary <= cur["dist"]:
                boundary += split_m
    if split["distance"] > 0:
        splits.append(_close(split))
    return splits
