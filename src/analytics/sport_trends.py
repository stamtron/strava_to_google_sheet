"""
Long-range multi-sport trends: run, bike and swim volume and efficiency per
month or quarter, with a year-over-year comparison.

Sessions come from Strava (summaries as stored) and from a Garmin export
(`fit_activities` rows). When both hold the same session — same sport group,
starts within FIT_MATCH_TOLERANCE_S — the Strava one wins, so the FIT history
only fills in what Strava doesn't have.

Efficiency per sport (higher is better unless noted):
- run:  speed per heartbeat, m/min per bpm (time-weighted over runs with HR);
- bike: watts per bpm, over rides with device power and HR;
- swim: pace in seconds per 100 m (lower is better).

Periods between the first and last session with nothing logged are returned
with zero volume rather than skipped, so gaps stay visible. Returns numbers and
status keys (`improved`, `declined`, `flat`, `insufficient`) only.
"""

from datetime import date, datetime, timedelta

from src.analytics.metrics import sport_group
from src.config import (
    FIT_MATCH_TOLERANCE_S,
    SPORT_TRENDS_COMPARE_DAYS,
    SPORT_TRENDS_FLAT_PCT,
    SPORT_TRENDS_MIN_MINUTES,
)
from src.formatting import distance_and_speed

TREND_GROUPS = ("run", "bike", "swim")
FIT_SPORT_GROUPS = {"running": "run", "cycling": "bike", "swimming": "swim"}
EFFICIENCY_UNITS = {"run": "m_per_min_per_bpm", "bike": "w_per_bpm", "swim": "s_per_100m"}
HIGHER_IS_BETTER = {"run": True, "bike": True, "swim": False}


def _utc_seconds(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _local_date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat((raw or "")[:10])
    except ValueError:
        return None


def _strava_session(act: dict) -> dict | None:
    group = sport_group(act.get("sport_type") or act.get("type") or "")
    day = _local_date(act.get("start_date_local"))
    if group not in TREND_GROUPS or day is None:
        return None
    distance, _speed = distance_and_speed(act)
    return {
        "group": group,
        "start": _utc_seconds(act.get("start_date")),
        "date": day,
        "secs": float(act.get("moving_time") or 0),
        "dist": distance,
        "hr": act.get("average_heartrate") if act.get("has_heartrate", True) else None,
        "power": act.get("average_watts") if group == "bike" and act.get("device_watts") else None,
        "source": "strava",
    }


def _fit_session(row: dict) -> dict | None:
    group = FIT_SPORT_GROUPS.get(row.get("sport"))
    day = _local_date(row.get("start_local"))
    if group is None or day is None:
        return None
    return {
        "group": group,
        "start": _utc_seconds(row.get("start_utc")),
        "date": day,
        "secs": float(row.get("moving_time") or 0),
        "dist": float(row.get("distance_m") or 0),
        "hr": row.get("avg_hr"),
        "power": row.get("avg_power") if group == "bike" else None,
        "source": "fit",
    }


def merge_sessions(strava_activities: list[dict], fit_activities: list[dict]) -> list[dict]:
    """Strava sessions plus the FIT sessions Strava doesn't already cover."""
    strava = [s for a in strava_activities or [] if (s := _strava_session(a))]
    starts = {g: sorted(s["start"] for s in strava if s["group"] == g and s["start"] is not None) for g in TREND_GROUPS}
    merged = list(strava)
    for row in fit_activities or []:
        s = _fit_session(row)
        if s is None:
            continue
        if s["start"] is not None and any(abs(t - s["start"]) <= FIT_MATCH_TOLERANCE_S for t in starts[s["group"]]):
            continue
        merged.append(s)
    return merged


def _period(day: date, bucket: str) -> str:
    if bucket == "quarter":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    return f"{day.year}-{day.month:02d}"


def _periods_between(first: date, last: date, bucket: str) -> list[str]:
    out, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        key = _period(date(year, month, 1), bucket)
        if not out or out[-1] != key:
            out.append(key)
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def _efficiency(group: str, sessions: list[dict]) -> float | None:
    """Time-weighted efficiency over sessions long enough to count."""
    long = [s for s in sessions if s["secs"] >= SPORT_TRENDS_MIN_MINUTES * 60]
    if group == "swim":
        swims = [s for s in long if s["dist"] > 0]
        dist = sum(s["dist"] for s in swims)
        return round(sum(s["secs"] for s in swims) / dist * 100, 1) if dist else None
    with_hr = [s for s in long if s["hr"]]
    if group == "bike":
        with_hr = [s for s in with_hr if s["power"]]
    secs = sum(s["secs"] for s in with_hr)
    if not secs:
        return None
    hr = sum(s["hr"] * s["secs"] for s in with_hr) / secs
    if group == "bike":
        power = sum(s["power"] * s["secs"] for s in with_hr) / secs
        return round(power / hr, 3)
    dist = sum(s["dist"] for s in with_hr)
    return round(dist / secs * 60 / hr, 3) if dist else None


def _stats(group: str, sessions: list[dict]) -> dict:
    secs = sum(s["secs"] for s in sessions)
    hr_sessions = [s for s in sessions if s["hr"]]
    hr_secs = sum(s["secs"] for s in hr_sessions)
    power_sessions = [s for s in sessions if s["power"]]
    power_secs = sum(s["secs"] for s in power_sessions)
    return {
        "sessions": len(sessions),
        "hours": round(secs / 3600, 1),
        "distance_km": round(sum(s["dist"] for s in sessions) / 1000, 1),
        "avg_hr": round(sum(s["hr"] * s["secs"] for s in hr_sessions) / hr_secs) if hr_secs else None,
        "avg_power": (
            round(sum(s["power"] * s["secs"] for s in power_sessions) / power_secs) if power_secs else None
        ),
        "efficiency": _efficiency(group, sessions),
        "fit_sessions": sum(1 for s in sessions if s["source"] == "fit"),
    }


def _pct_change(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return round((now - before) / before * 100, 1)


def _efficiency_status(group: str, change_pct: float | None) -> str:
    if change_pct is None:
        return "insufficient"
    signed = change_pct if HIGHER_IS_BETTER[group] else -change_pct
    if signed > SPORT_TRENDS_FLAT_PCT:
        return "improved"
    if signed < -SPORT_TRENDS_FLAT_PCT:
        return "declined"
    return "flat"


def _year_over_year(group: str, sessions: list[dict], today: date) -> dict:
    """Trailing window vs the same window one year earlier."""
    days = SPORT_TRENDS_COMPARE_DAYS
    start = today - timedelta(days=days - 1)
    prev_start, prev_end = start - timedelta(days=365), today - timedelta(days=365)
    now = _stats(group, [s for s in sessions if start <= s["date"] <= today])
    before = _stats(group, [s for s in sessions if prev_start <= s["date"] <= prev_end])
    eff_change = _pct_change(now["efficiency"], before["efficiency"])
    return {
        "window_days": days,
        "current": now,
        "previous": before,
        "hours_change_pct": _pct_change(now["hours"], before["hours"]),
        "efficiency_change_pct": eff_change,
        "efficiency_status": _efficiency_status(group, eff_change),
    }


def sport_trends(
    strava_activities: list[dict],
    fit_activities: list[dict],
    today: date,
    bucket: str = "month",
) -> dict:
    """
    Per-sport trend series and year-over-year comparison.

    Returns {"bucket", "periods", "sports": {group: {"series": [...],
    "efficiency_unit", "higher_is_better", "year_over_year"}}, "sources"}.
    `periods` spans the first to the last session (any sport); each series
    has one entry per period.
    """
    if bucket not in ("month", "quarter"):
        raise ValueError(f"unknown bucket {bucket!r}")
    sessions = [s for s in merge_sessions(strava_activities, fit_activities) if s["date"] <= today]
    sources = {"strava": sum(s["source"] == "strava" for s in sessions), "fit": sum(s["source"] == "fit" for s in sessions)}
    if not sessions:
        return {"bucket": bucket, "periods": [], "sports": {}, "sources": sources}

    periods = _periods_between(min(s["date"] for s in sessions), max(s["date"] for s in sessions), bucket)
    sports = {}
    for group in TREND_GROUPS:
        mine = [s for s in sessions if s["group"] == group]
        by_period: dict[str, list[dict]] = {p: [] for p in periods}
        for s in mine:
            by_period[_period(s["date"], bucket)].append(s)
        sports[group] = {
            "series": [{"period": p, **_stats(group, by_period[p])} for p in periods],
            "efficiency_unit": EFFICIENCY_UNITS[group],
            "higher_is_better": HIGHER_IS_BETTER[group],
            "year_over_year": _year_over_year(group, mine, today),
        }
    return {"bucket": bucket, "periods": periods, "sports": sports, "sources": sources}
