"""Dashboard analytics: weekly metrics, fitness/fatigue/form, durability, weather."""

import logging
from datetime import date

from fastapi import APIRouter, Query

from src.analytics.ai_coach import predict_race_performances, predict_triathlon_performances
from src.analytics.decoupling import decoupling_trend
from src.analytics.durability import assess_run_durability, sport_strength_profile, suggest_cross_training
from src.analytics.metrics import (
    build_progression_history,
    calculate_acwr,
    calculate_fitness_fatigue,
    calculate_hr_zones,
    calculate_polarized_distribution,
    process_activities_into_weeks,
)
from src.api.activity_data import get_summaries, read_history
from src.api.deps import internal_error
from src.config import (
    ATHLETE_GOAL_RACES,
    GOAL_70_3_DATE,
    GOAL_HALF_MARATHON_DATE,
    HR_MAX,
    HR_REST,
    TRAINING_PHASE,
)
from src.integrations.garmin import _load_garmin_cache, compute_biometric_baselines, get_weekly_health_summaries
from src.integrations.weather import get_weather_outlook
from src.timeutil import now_local

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": now_local().isoformat()}


@router.get("/api/activities")
def get_activities(count: int = Query(200, ge=1, le=500)):
    """Fetch recent Strava activities with local caching."""
    summaries, details = get_summaries(count)
    return {"activities": summaries, "details": details}


@router.get("/api/dashboard")
def get_dashboard_data(count: int = Query(200, ge=1, le=500)):
    """Consolidated training metrics, Relative Effort, progression history & Garmin health."""
    summaries, _ = get_summaries(count)

    # Process activities into weeks with Relative Effort & Volumes
    weeks = process_activities_into_weeks(summaries)
    sorted_week_keys = sorted(weeks.keys())

    # Acute:Chronic Workload Ratio — computed once and shared with the
    # progression history, which would otherwise recalculate it.
    acwr_map = calculate_acwr(sorted_week_keys, weeks)
    for w_key in sorted_week_keys:
        weeks[w_key]["acwr"] = acwr_map.get(w_key, {})
        weeks[w_key]["polarized"] = calculate_polarized_distribution(
            weeks[w_key].get("activities", []),
            hr_max=HR_MAX,
            hr_rest=HR_REST,
        )

    # Garmin health: always return all disk-cached weeks. For uncached weeks,
    # only query the active/recent weeks (at most 1 uncached week per request)
    # so that historical activity queries never block the dashboard on minutes of sequential API calls.
    cached_garmin = _load_garmin_cache()
    active_weeks = set(sorted_week_keys[-2:]) if sorted_week_keys else set()
    week_ranges = {
        w_key: (
            date.fromisoformat(weeks[w_key]["week_monday"]),
            date.fromisoformat(weeks[w_key]["week_sunday"]),
        )
        for w_key in sorted_week_keys
        if w_key in cached_garmin or w_key in active_weeks
    }
    try:
        garmin_summaries = get_weekly_health_summaries(week_ranges, max_fetch=1)
    except Exception as e:
        logger.warning("Garmin unavailable: %s", e)
        garmin_summaries = {}

    try:
        weather_outlook = get_weather_outlook(past_days=2, forecast_days=7)
    except Exception as e:
        logger.warning("Weather fetch failed: %s", e)
        weather_outlook = []
    try:
        biometric_baselines = compute_biometric_baselines(garmin_summaries)
    except Exception as e:
        logger.warning("Baselines computation failed: %s", e)
        biometric_baselines = {}

    try:
        analytics = {
            "performance": calculate_fitness_fatigue(summaries),
            "hr_zones": calculate_hr_zones(hr_max=HR_MAX, hr_rest=HR_REST),
            "predictions": predict_race_performances(summaries),
            "triathlon": predict_triathlon_performances(activities=summaries, use_race_pb=False),
            "triathlon_pb": predict_triathlon_performances(activities=summaries, use_race_pb=True),
            "progression": build_progression_history(weeks, acwr_map=acwr_map),
        }
    except Exception as e:
        raise internal_error("Dashboard analytics") from e

    return {
        "weeks": weeks,
        "garmin": garmin_summaries,
        "baselines": biometric_baselines,
        "weather": weather_outlook,
        "season": {
            "training_phase": TRAINING_PHASE,
            "goal_races": ATHLETE_GOAL_RACES,
            "half_marathon_date": GOAL_HALF_MARATHON_DATE,
            "half_ironman_date": GOAL_70_3_DATE,
        },
        **analytics,
        "activities_total": len(summaries),
    }


@router.get("/api/performance")
def get_performance_model(
    count: int = Query(200, ge=1, le=500),
    days: int = Query(90, ge=14, le=365),
):
    """Daily Fitness (CTL), Fatigue (ATL), and Form (TSB) impulse-response series."""
    summaries, _ = get_summaries(count)
    return calculate_fitness_fatigue(summaries, display_days=days)


@router.get("/api/weather")
def get_weather(
    past_days: int = Query(3, ge=0, le=14),
    forecast_days: int = Query(7, ge=1, le=14),
    refresh: bool = Query(False),
):
    """Daily historical and forecast weather for the athlete's home city (Athens, Greece)."""
    try:
        outlook = get_weather_outlook(
            past_days=past_days,
            forecast_days=forecast_days,
            force_refresh=refresh,
        )
        return {"city": "Athens, Greece", "days": outlook}
    except Exception as e:
        raise internal_error("Weather fetch") from e


@router.get("/api/durability")
def get_run_durability(count: int = Query(200, ge=1, le=500)):
    """
    Run-specific load management: ramp rate, spacing, long-run share, monotony,
    and the cross-training plan that keeps the aerobic dose while lowering impact.

    Separate from `/api/dashboard` because it answers a different question. The
    dashboard reports what was done; this reports whether the legs can take more.
    """
    summaries, _ = get_summaries(count)

    weeks = process_activities_into_weeks(summaries)
    sorted_week_keys = sorted(weeks.keys())
    acwr_run = calculate_acwr(sorted_week_keys, weeks, effort_key="run_relative_effort")

    durability = assess_run_durability(weeks, acwr_run)
    profile = sport_strength_profile(summaries)

    # The target is the athlete's own recent run load, not an arbitrary goal:
    # the question being answered is "can I keep doing what I'm doing?"
    recent = [weeks[k].get("run_relative_effort", 0.0) for k in sorted_week_keys[-4:]]
    target_run_load = round(max(recent), 1) if recent else 0.0

    return {
        "durability": durability,
        "profile": profile,
        "acwr_run": acwr_run,
        "cross_training": suggest_cross_training(target_run_load, durability, profile),
        "weeks_analyzed": len(sorted_week_keys),
    }


@router.get("/api/decoupling")
def get_aerobic_decoupling(count: int = Query(200, ge=1, le=500)):
    """
    Pa:HR aerobic decoupling trend on long steady runs (from per-km splits).

    Details come from the hot cache and the history store combined — the cache
    only holds what the last sync fetched, the store keeps every detail seen.
    """
    try:
        summaries, cached_details = get_summaries(count)
        _, stored_details = read_history(count)
        return decoupling_trend(summaries, {**stored_details, **cached_details})
    except Exception as e:
        raise internal_error("Decoupling trend") from e
