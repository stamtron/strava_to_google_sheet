"""
Aerobic decoupling (Pa:HR) on long steady runs.

Efficiency factor (EF) is speed divided by heart rate. On a steady aerobic run
a well-conditioned engine holds EF from the first half to the second; when the
aerobic base is thin, HR drifts up at the same pace and EF drops. Decoupling is
that drop as a percentage of the first-half EF:

    decoupling % = (EF_first - EF_second) / EF_first * 100

Computed from Strava's per-km `splits_metric` (in the activity detail payload),
time-weighted so a short final split doesn't count like a full kilometre. Only
long, steady runs qualify — intervals and progressions would drift by design.

Returns numbers and status keys (`good`, `moderate`, `high`) only.
"""

from statistics import mean, pstdev

from src.analytics.metrics import sport_group
from src.config import (
    DECOUPLING_GOOD_PCT,
    DECOUPLING_HIGH_PCT,
    DECOUPLING_MAX_PACE_CV,
    DECOUPLING_MIN_MINUTES,
    DECOUPLING_MIN_SPLIT_KM,
    DECOUPLING_MIN_SPLITS,
    DECOUPLING_TREND_RUNS,
)


def _usable_splits(splits: list[dict]) -> list[dict]:
    """Splits with distance, moving time, speed and HR; drops the partial tail."""
    out = []
    for s in splits or []:
        dist = s.get("distance") or 0
        secs = s.get("moving_time") or s.get("elapsed_time") or 0
        hr = s.get("average_heartrate")
        if dist / 1000 < DECOUPLING_MIN_SPLIT_KM or secs <= 0 or not hr:
            continue
        out.append({"secs": float(secs), "speed": dist / secs, "hr": float(hr)})
    return out


def _weighted_ef(splits: list[dict]) -> float | None:
    total = sum(s["secs"] for s in splits)
    if total <= 0:
        return None
    speed = sum(s["speed"] * s["secs"] for s in splits) / total
    hr = sum(s["hr"] * s["secs"] for s in splits) / total
    return speed / hr if hr else None


def classify_decoupling(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct < DECOUPLING_GOOD_PCT:
        return "good"
    if pct < DECOUPLING_HIGH_PCT:
        return "moderate"
    return "high"


def run_decoupling(splits: list[dict]) -> dict | None:
    """
    Pa:HR decoupling for one run's per-km splits, or None if it doesn't qualify
    (too short, too few HR splits, or unsteady pace).
    """
    usable = _usable_splits(splits)
    if len(usable) < DECOUPLING_MIN_SPLITS:
        return None
    total_secs = sum(s["secs"] for s in usable)
    if total_secs / 60 < DECOUPLING_MIN_MINUTES:
        return None

    speeds = [s["speed"] for s in usable]
    pace_cv = pstdev(speeds) / mean(speeds)
    if pace_cv > DECOUPLING_MAX_PACE_CV:
        return None

    # Split by time, not split count: halves of equal duration.
    first, second, elapsed = [], [], 0.0
    for s in usable:
        (first if elapsed + s["secs"] / 2 <= total_secs / 2 else second).append(s)
        elapsed += s["secs"]
    ef_first, ef_second = _weighted_ef(first), _weighted_ef(second)
    if not ef_first or not ef_second:
        return None

    pct = round((ef_first - ef_second) / ef_first * 100, 1)
    return {
        "decoupling_pct": pct,
        "status": classify_decoupling(pct),
        # EF in metres per minute per beat — the conventional readable scale.
        "ef_first": round(ef_first * 60, 3),
        "ef_second": round(ef_second * 60, 3),
        "pace_cv": round(pace_cv, 3),
        "duration_min": round(total_secs / 60, 1),
    }


def decoupling_trend(activities: list[dict], details: dict, limit: int = DECOUPLING_TREND_RUNS) -> dict:
    """
    Decoupling for the most recent qualifying runs, oldest first.

    `details` maps activity id (str or int) to the Strava detail payload.
    `runs_without_detail` counts recent runs that couldn't be assessed because
    no detail (splits) has been fetched for them yet.
    """
    runs, missing_detail = [], 0
    for act in activities:
        if sport_group(act.get("sport_type") or act.get("type") or "") != "run":
            continue
        if (act.get("moving_time") or 0) / 60 < DECOUPLING_MIN_MINUTES:
            continue
        act_id = act.get("id")
        detail = details.get(str(act_id)) or details.get(act_id) or {}
        splits = detail.get("splits_metric")
        if not splits:
            missing_detail += 1
            continue
        result = run_decoupling(splits)
        if result is None:
            continue
        runs.append({
            "id": act_id,
            "date": (act.get("start_date_local") or "")[:10],
            "name": act.get("name"),
            "distance_km": round((act.get("distance") or 0) / 1000, 2),
            **result,
        })

    runs.sort(key=lambda r: r["date"])
    runs = runs[-limit:]
    pcts = [r["decoupling_pct"] for r in runs]
    recent = pcts[-3:]
    return {
        "runs": runs,
        "count": len(runs),
        "latest_pct": pcts[-1] if pcts else None,
        "recent_avg_pct": round(mean(recent), 1) if recent else None,
        "recent_status": classify_decoupling(round(mean(recent), 1) if recent else None),
        "runs_without_detail": missing_detail,
        "good_pct": DECOUPLING_GOOD_PCT,
        "high_pct": DECOUPLING_HIGH_PCT,
    }
