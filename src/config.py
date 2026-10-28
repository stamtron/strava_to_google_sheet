"""
Centralized Configuration and Settings.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Project Root Directory
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Load .env from project root
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back to default on bad input."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to default on bad input."""
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    """Read a comma-separated list from the environment."""
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean from the environment."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


# API Keys & Credentials
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# File Paths
TOKEN_FILE = str(PROJECT_ROOT / "token.json")
CREDENTIALS_FILE = str(PROJECT_ROOT / "credentials.json")
GSHEETS_TOKEN_FILE = str(PROJECT_ROOT / "gsheets_token.json")
GARMIN_TOKEN_DIR = str(PROJECT_ROOT / ".garmin_tokens")
ACTIVITIES_CACHE_FILE = str(PROJECT_ROOT / ".activities_cache.json")
GARMIN_CACHE_FILE = str(PROJECT_ROOT / ".garmin_cache.json")
HISTORY_DB_FILE = str(PROJECT_ROOT / ".training_history.db")
COACH_MEMORY_DIR = str(PROJECT_ROOT / ".coach_memory")
WEB_DIR = str(PROJECT_ROOT / "web")

# Google Sheets Configuration
SHEET_NAME = "Προπόνηση-Ανατροφοδότηση"
FIRST_WEEK_ROW = 13
ROWS_PER_WEEK = 1
GSHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Web Server.
# Bound to loopback by default: the API serves training and biometric data with
# no authentication, so it should not listen on the LAN unless explicitly asked.
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = _env_int("SERVER_PORT", 8000)
ALLOWED_ORIGINS = _env_list(
    "ALLOWED_ORIGINS",
    [f"http://localhost:{SERVER_PORT}", f"http://127.0.0.1:{SERVER_PORT}"],
)

# Strava OAuth callback listener.
# Must differ from SERVER_PORT: the dashboard holds that port, and the callback
# handler blocks the thread while waiting for the browser redirect.
STRAVA_REDIRECT_PORT = _env_int("STRAVA_REDIRECT_PORT", 8123)

# Strava rate limiting (official limits: 200 requests / 15 min, 2000 / day).
STRAVA_DETAIL_DELAY_SEC = _env_float("STRAVA_DETAIL_DELAY_SEC", 0.15)
STRAVA_MAX_RETRIES = _env_int("STRAVA_MAX_RETRIES", 3)

# Full-history backfill. Strava caps `per_page` at 200; the page cap is a
# runaway guard, not a real limit — at 200/page it covers 20 000 activities.
STRAVA_BACKFILL_PAGE_SIZE = _env_int("STRAVA_BACKFILL_PAGE_SIZE", 200)
STRAVA_BACKFILL_MAX_PAGES = _env_int("STRAVA_BACKFILL_MAX_PAGES", 100)
STRAVA_BACKFILL_PAGE_DELAY_SEC = _env_float("STRAVA_BACKFILL_PAGE_DELAY_SEC", 0.5)

# Cache lifetimes (seconds)
ACTIVITIES_CACHE_TTL = _env_int("ACTIVITIES_CACHE_TTL", 600)
GARMIN_CACHE_TTL = _env_int("GARMIN_CACHE_TTL", 21600)  # 6h
WEATHER_CACHE_TTL = _env_int("WEATHER_CACHE_TTL", 10800)  # 3h for forecasts

# Athlete Location & Weather
ATHLETE_CITY = os.getenv("ATHLETE_CITY", "Athens, Greece").strip()
ATHLETE_LATITUDE = _env_float("ATHLETE_LATITUDE", 37.9838)
ATHLETE_LONGITUDE = _env_float("ATHLETE_LONGITUDE", 23.7275)
ATHLETE_TIMEZONE = os.getenv("ATHLETE_TIMEZONE", "Europe/Athens").strip()
WEATHER_CACHE_FILE = os.path.join(PROJECT_ROOT, ".weather_cache.json")

# Athlete physiology — used by the TRIMP relative-effort estimate.
HR_MAX = _env_int("HR_MAX", 185)
HR_REST = _env_int("HR_REST", 50)

# Sport-specific data corrections.
# Strava reports this athlete's pool swims at double the true distance, so raw
# swim distance and speed are divided before use.
SWIM_DISTANCE_DIVISOR = _env_float("SWIM_DISTANCE_DIVISOR", 2.0)
# Indoor trainer rides report ~0 distance, so distance is estimated from
# duration at a nominal steady speed.
INDOOR_BIKE_SPEED_KMH = _env_float("INDOOR_BIKE_SPEED_KMH", 21.0)

# ACWR (Acute:Chronic Workload Ratio) configuration.
# Chronic load is the mean weekly load over the ACWR_CHRONIC_WEEKS weeks
# *preceding* the week being scored; fewer than ACWR_MIN_CHRONIC_WEEKS of
# history yields no ratio rather than a misleading one.
ACWR_CHRONIC_WEEKS = _env_int("ACWR_CHRONIC_WEEKS", 4)
ACWR_MIN_CHRONIC_WEEKS = _env_int("ACWR_MIN_CHRONIC_WEEKS", 2)

# Run durability thresholds.
# The classic "10% rule" for week-over-week run volume. It is a guideline rather
# than a law, so it is reported as a signal, not a hard cap.
RUN_RAMP_SAFE_PCT = _env_float("RUN_RAMP_SAFE_PCT", 10.0)
# Fraction of weekly run volume the longest run may occupy before it stops being
# an endurance stimulus and starts being the week's main injury exposure.
RUN_LONG_RUN_MAX_SHARE = _env_float("RUN_LONG_RUN_MAX_SHARE", 0.40)
# Non-running days per week that impact-sensitive athletes need for tissue
# remodelling. Back-to-back run days are a stronger injury signal than volume.
RUN_MIN_REST_DAYS = _env_int("RUN_MIN_REST_DAYS", 2)
# Foster monotony (mean daily load / SD) and strain (weekly load × monotony).
# Monotony above ~2.0 means every day looks the same, which suppresses recovery
# even when total volume is modest.
MONOTONY_WARN_THRESHOLD = _env_float("MONOTONY_WARN_THRESHOLD", 2.0)
STRAIN_WARN_THRESHOLD = _env_float("STRAIN_WARN_THRESHOLD", 1500.0)

# Cross-training substitution factors: how much run-equivalent aerobic stimulus
# one minute of the alternative buys, with none of the impact loading. Aqua
# jogging replicates the run motion almost exactly; cycling needs longer to
# deliver the same aerobic dose.
AQUA_JOG_LOAD_FACTOR = _env_float("AQUA_JOG_LOAD_FACTOR", 0.90)
BIKE_RUN_LOAD_FACTOR = _env_float("BIKE_RUN_LOAD_FACTOR", 0.55)
CROSS_TRAINING_AQUA_SHARE = _env_float("CROSS_TRAINING_AQUA_SHARE", 0.60)
RUN_RETENTION_BY_RISK = {
    "low": _env_float("RUN_RETENTION_LOW", 1.0),
    "moderate": _env_float("RUN_RETENTION_MODERATE", 0.8),
    "high": _env_float("RUN_RETENTION_HIGH", 0.6),
    "unknown": _env_float("RUN_RETENTION_UNKNOWN", 0.9),
}


# Gemini models tried in order for AI coaching. Google retires model IDs on a
# rolling basis, so the list is a fallback chain and the newest generation goes first.
GEMINI_MODELS = _env_list("GEMINI_MODELS", ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"])

# Conversational coach (POST /api/ai/chat).
# A separate model list from GEMINI_MODELS: the chat agent needs function
# calling, so a model that can serve the one-shot weekly panel is not
# automatically a valid choice here.
COACH_CHAT_MODELS = _env_list("COACH_CHAT_MODELS", ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"])
# Ceiling on the automatic tool-calling loop for one turn. Without it a model
# that keeps re-querying its own data can spend an unbounded number of requests
# on a single question.
COACH_MAX_TOOL_CALLS = _env_int("COACH_MAX_TOOL_CALLS", 8)
# How long an idle conversation survives, in seconds (default 7 days). Measured
# from the session's last message, not its first.
COACH_SESSION_TTL = _env_int("COACH_SESSION_TTL", 604800)
# Most recent messages replayed as history on each turn. Bounds the tokens sent
# per request; older turns are still on disk, just not in the prompt.
COACH_MAX_HISTORY_MESSAGES = _env_int("COACH_MAX_HISTORY_MESSAGES", 24)
# Durable facts recalled from memory and injected into the system instruction.
COACH_MEMORY_TOP_K = _env_int("COACH_MEMORY_TOP_K", 5)
# Which memory implementation backs `remember_fact`. Both expose the same four
# methods; "chroma" recalls by meaning and costs one embedding request per write
# and per turn, "sqlite" recalls by keyword overlap and costs nothing.
COACH_MEMORY_BACKEND = os.getenv("COACH_MEMORY_BACKEND", "sqlite").strip().lower()
# Embedding model for the Chroma memory. Chroma's own default would pull
# onnxruntime and download model weights on first use; the Gemini key is already
# here, so it is used instead.
COACH_EMBEDDING_MODEL = os.getenv("COACH_EMBEDDING_MODEL", "gemini-embedding-001")
COACH_MEMORY_COLLECTION = os.getenv("COACH_MEMORY_COLLECTION", "athlete_memory")
# Ceiling on facts the end-of-conversation extraction pass may store from one
# transcript. A model asked for "durable facts" will otherwise file the whole
# conversation, and a memory of everything recalls nothing useful.
COACH_AUTO_FACT_LIMIT = _env_int("COACH_AUTO_FACT_LIMIT", 5)

# Sport type groupings
RUN_SPORTS = ("Run", "TrailRun")
BIKE_SPORTS = ("Ride", "VirtualRide", "GravelRide", "MountainBikeRide")
SWIM_SPORTS = ("Swim",)
STRENGTH_SPORTS = ("WeightTraining", "Workout")

# Athlete Verified Personal Bests (PBs)
ATHLETE_PB_HALF_MARATHON_SEC = _env_int("ATHLETE_PB_HALF_MARATHON_SEC", 6415) # 1h 46m 55s (Athens Half Marathon 2026)
ATHLETE_PB_10K_SEC = _env_int("ATHLETE_PB_10K_SEC", 3015)                     # 50m 15s (Kapodistrias 2026)
ATHLETE_PB_5K_SEC = _env_int("ATHLETE_PB_5K_SEC", 1395)                       # 23m 15s (4:39/km equivalent)
ATHLETE_PB_SPRINT_TRI_SEC = _env_int("ATHLETE_PB_SPRINT_TRI_SEC", 4532)        # 1h 15m 32s (Lake Doxa 2026)
ATHLETE_PB_OLYMPIC_TRI_SEC = _env_int("ATHLETE_PB_OLYMPIC_TRI_SEC", 8647)      # 2h 24m 07s (Messolonghi 2026)
ATHLETE_PB_AQUATHLON_SEC = _env_int("ATHLETE_PB_AQUATHLON_SEC", 2234)          # 37m 14s (Full Moon 2026)

# Race-day swim and bike baselines for the PB-calibrated triathlon projection.
# Separate constants rather than derived from the triathlon PBs above: a finish
# time cannot be split back into legs, and no standalone swim or bike race has
# been recorded. The run baseline is derived from ATHLETE_PB_5K_SEC instead.
ATHLETE_RACE_SWIM_100M_SEC = _env_float("ATHLETE_RACE_SWIM_100M_SEC", 100.0)   # 1:40 /100m
ATHLETE_RACE_BIKE_SPEED_KMH = _env_float("ATHLETE_RACE_BIKE_SPEED_KMH", 32.5)

# Strava Webhook Real-Time Sync
STRAVA_WEBHOOK_VERIFY_TOKEN = os.getenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "STRAVA_WEBHOOK_SECRET").strip()
STRAVA_WEBHOOK_SECRET_PARAM = os.getenv("STRAVA_WEBHOOK_SECRET_PARAM", "").strip()
AUTO_SYNC_SHEET_ON_WEBHOOK = _env_bool("AUTO_SYNC_SHEET_ON_WEBHOOK", False)

# Telegram Notifications & Next-Day Workout Dispatcher
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_DAILY_DISPATCH_TIME = os.getenv("TELEGRAM_DAILY_DISPATCH_TIME", "20:30").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

# Daily Brief Weather & Tip Thresholds
BRIEF_RAIN_THRESHOLD_MM = _env_float("BRIEF_RAIN_THRESHOLD_MM", 2.0)
BRIEF_HEAT_THRESHOLD_C = _env_float("BRIEF_HEAT_THRESHOLD_C", 32.0)
DEFAULT_COACH_TIP = os.getenv(
    "DEFAULT_COACH_TIP",
    "Keep easy aerobic pace in Zone 2 for optimal recovery and mitochondrial adaptation.",
).strip()
DEFAULT_RAIN_TIP = os.getenv(
    "DEFAULT_RAIN_TIP",
    "Rain expected; check tire pressure for wet roads or consider indoor trainer.",
).strip()
DEFAULT_HEAT_TIP = os.getenv(
    "DEFAULT_HEAT_TIP",
    "High heat expected; hydrate well and start early morning.",
).strip()

# Google Sheets API resilience
GSHEETS_MAX_RETRIES = _env_int("GSHEETS_MAX_RETRIES", 3)

# Gear & shoe alerts
SHOE_ALERT_KM = _env_int("SHOE_ALERT_KM", 650)

# Fitness & Fatigue (Banister Impulse-Response EWMA) Model
CTL_DAYS = _env_int("CTL_DAYS", 42)  # Chronic Training Load (Fitness) time constant
ATL_DAYS = _env_int("ATL_DAYS", 7)   # Acute Training Load (Fatigue) time constant
TSB_OPTIMAL_MIN = _env_float("TSB_OPTIMAL_MIN", -30.0)
TSB_OPTIMAL_MAX = _env_float("TSB_OPTIMAL_MAX", -10.0)
TSB_FRESH_MIN = _env_float("TSB_FRESH_MIN", 10.0)
TSB_FRESH_MAX = _env_float("TSB_FRESH_MAX", 25.0)

# Weekly Telegram Dispatch Time (e.g. Sunday evening 21:00)
TELEGRAM_WEEKLY_DISPATCH_TIME = os.getenv("TELEGRAM_WEEKLY_DISPATCH_TIME", "21:00").strip()


# --- Integration tunables ---
# HTTP timeouts and retry backoff for external services
STRAVA_HTTP_TIMEOUT_SEC = _env_float("STRAVA_HTTP_TIMEOUT_SEC", 30.0)
STRAVA_BACKOFF_BASE_SEC = _env_float("STRAVA_BACKOFF_BASE_SEC", 2.0)
STRAVA_BACKOFF_CAP_SEC = _env_float("STRAVA_BACKOFF_CAP_SEC", 30.0)
WEATHER_HTTP_TIMEOUT_SEC = _env_float("WEATHER_HTTP_TIMEOUT_SEC", 6.0)
GSHEETS_RETRY_DELAY_SEC = _env_float("GSHEETS_RETRY_DELAY_SEC", 1.5)
TELEGRAM_HTTP_TIMEOUT_SEC = _env_float("TELEGRAM_HTTP_TIMEOUT_SEC", 10.0)

# Telegram message limits & bot loop
TELEGRAM_MAX_MESSAGE_CHARS = _env_int("TELEGRAM_MAX_MESSAGE_CHARS", 4000)  # API hard limit is 4096
TELEGRAM_POLL_TIMEOUT_SEC = _env_int("TELEGRAM_POLL_TIMEOUT_SEC", 10)
TELEGRAM_BOT_LOOP_SLEEP_SEC = _env_float("TELEGRAM_BOT_LOOP_SLEEP_SEC", 0.5)
TELEGRAM_BOT_ERROR_SLEEP_SEC = _env_float("TELEGRAM_BOT_ERROR_SLEEP_SEC", 5.0)
TELEGRAM_DISPATCH_RETRY_BACKOFF_SEC = _env_int("TELEGRAM_DISPATCH_RETRY_BACKOFF_SEC", 900)
TELEGRAM_DAILY_DISPATCH_FALLBACK = "20:30"
TELEGRAM_WEEKLY_DISPATCH_FALLBACK = "21:00"

# Google Sheets plan layout (first row reuses FIRST_WEEK_ROW)
PLAN_LAST_ROW = _env_int("PLAN_LAST_ROW", 150)

# Garmin client & cache
GARMIN_LOGIN_RETRY_COOLDOWN_SEC = _env_int("GARMIN_LOGIN_RETRY_COOLDOWN_SEC", 900)
GARMIN_EMPTY_CACHE_TTL = _env_int("GARMIN_EMPTY_CACHE_TTL", 3600)  # null/partial weeks

# Garmin biometric baselines (mean ± factor * std)
BASELINE_STD_FACTOR = _env_float("BASELINE_STD_FACTOR", 0.75)
BASELINE_STD_FLOOR = _env_float("BASELINE_STD_FLOOR", 2.0)
BASELINE_LOW_FLOOR = _env_float("BASELINE_LOW_FLOOR", 10.0)
BASELINE_MIN_WEEKS = _env_int("BASELINE_MIN_WEEKS", 3)

# Garmin daily recovery score thresholds
RECOVERY_BASE_SCORE = _env_int("RECOVERY_BASE_SCORE", 80)
RECOVERY_SCORE_MIN = _env_int("RECOVERY_SCORE_MIN", 10)
RECOVERY_SCORE_MAX = _env_int("RECOVERY_SCORE_MAX", 100)
RECOVERY_OPTIMAL_MIN = _env_int("RECOVERY_OPTIMAL_MIN", 80)
RECOVERY_ADEQUATE_MIN = _env_int("RECOVERY_ADEQUATE_MIN", 65)
RECOVERY_COMPROMISED_MIN = _env_int("RECOVERY_COMPROMISED_MIN", 45)
RECOVERY_SLEEP_CRITICAL_H = _env_float("RECOVERY_SLEEP_CRITICAL_H", 5.5)
RECOVERY_SLEEP_LOW_H = _env_float("RECOVERY_SLEEP_LOW_H", 6.5)
RECOVERY_SLEEP_GOOD_H = _env_float("RECOVERY_SLEEP_GOOD_H", 7.5)
RECOVERY_RHR_HIGH_BPM = _env_int("RECOVERY_RHR_HIGH_BPM", 60)
RECOVERY_RHR_LOW_BPM = _env_int("RECOVERY_RHR_LOW_BPM", 50)
RECOVERY_BB_DEPLETED = _env_int("RECOVERY_BB_DEPLETED", 30)
RECOVERY_BB_MODERATE = _env_int("RECOVERY_BB_MODERATE", 50)
RECOVERY_BB_HIGH = _env_int("RECOVERY_BB_HIGH", 75)
RECOVERY_UNKNOWN_SCORE = _env_int("RECOVERY_UNKNOWN_SCORE", 75)  # shown when Garmin has no data

# Structured workout generation
DEFAULT_RUN_PACE_SEC_PER_KM = _env_float("DEFAULT_RUN_PACE_SEC_PER_KM", 315.0)  # 5:15/km
WORKOUT_SPEED_TOLERANCE_MS = _env_float("WORKOUT_SPEED_TOLERANCE_MS", 0.10)
GARMIN_WORKOUT_NAME_MAX_CHARS = _env_int("GARMIN_WORKOUT_NAME_MAX_CHARS", 35)

# --- Analytics thresholds ---
# Strava's `kilojoules` is mechanical work at the pedals. Human gross efficiency
# (~20-25%) cancels the 4.184 kJ/kcal conversion almost exactly, so the
# convention is kcal ≈ kJ. Applied only when Strava reports no `calories`.
KJ_TO_KCAL_FACTOR = _env_float("KJ_TO_KCAL_FACTOR", 1.0)

# ACWR band upper edges (inclusive): low ≤ LOW_MAX < optimal ≤ OPTIMAL_MAX <
# overreaching ≤ OVERREACHING_MAX < spike.
ACWR_LOW_MAX = _env_float("ACWR_LOW_MAX", 0.8)
ACWR_OPTIMAL_MAX = _env_float("ACWR_OPTIMAL_MAX", 1.3)
ACWR_OVERREACHING_MAX = _env_float("ACWR_OVERREACHING_MAX", 1.5)

# Banister TRIMP estimate used when Strava has no suffer score:
# load/min = hrr · WEIGHT · e^(EXPONENT · hrr), with hrr clamped to ≥ HRR_FLOOR.
# Without heart rate, load falls back to DURATION_ONLY_PER_MIN per minute.
TRIMP_HRR_FLOOR = _env_float("TRIMP_HRR_FLOOR", 0.4)
TRIMP_WEIGHT = _env_float("TRIMP_WEIGHT", 0.64)
TRIMP_EXPONENT = _env_float("TRIMP_EXPONENT", 1.92)
TRIMP_DURATION_ONLY_PER_MIN = _env_float("TRIMP_DURATION_ONLY_PER_MIN", 0.5)

# Floor on heart-rate reserve (bpm) so a misconfigured HR_MAX/HR_REST pair
# cannot produce a near-zero denominator.
MIN_HRR_BPM = _env_int("MIN_HRR_BPM", 20)

# RPE (Borg CR10) estimation anchors: (input, rpe) points, linearly
# interpolated and clamped at both ends. Input is %HRR for the heart-rate path
# and suffer score per minute for the fallback path.
RPE_HRR_ANCHORS = (
    (0.0, 1.0),
    (_env_float("RPE_HRR_EASY", 0.40), 2.0),
    (_env_float("RPE_HRR_MODERATE", 0.60), 4.0),
    (_env_float("RPE_HRR_TEMPO", 0.75), 6.0),
    (_env_float("RPE_HRR_THRESHOLD", 0.85), 8.0),
    (1.0, 10.0),
)
RPE_SUFFER_PER_MIN_ANCHORS = (
    (0.0, 1.0),
    (_env_float("RPE_SUFFER_EASY", 0.25), 3.0),
    (_env_float("RPE_SUFFER_MODERATE", 0.50), 5.0),
    (_env_float("RPE_SUFFER_HARD", 0.80), 7.0),
    (_env_float("RPE_SUFFER_VERY_HARD", 1.20), 9.0),
    (_env_float("RPE_SUFFER_MAX", 2.00), 10.0),
)

# Karvonen zone lower edges as %HRR: Z1 starts at the first, Z5 at the last.
HR_ZONE_HRR_EDGES = (
    _env_float("HR_Z1_HRR", 0.50),
    _env_float("HR_Z2_HRR", 0.60),
    _env_float("HR_Z3_HRR", 0.72),
    _env_float("HR_Z4_HRR", 0.82),
    _env_float("HR_Z5_HRR", 0.90),
)
# How a session's time is spread across Z1-Z5 given which zone its average HR
# falls in (row = zone of the average, Z1 row also covers anything below Z2).
HR_ZONE_TIME_SPREAD = (
    (0.75, 0.25, 0.00, 0.00, 0.00),
    (0.20, 0.70, 0.10, 0.00, 0.00),
    (0.10, 0.25, 0.55, 0.10, 0.00),
    (0.00, 0.10, 0.25, 0.55, 0.10),
    (0.00, 0.00, 0.10, 0.30, 0.60),
)

# Seiler intensity-distribution classification (percent of time).
POLARIZED_LOW_MIN_PCT = _env_float("POLARIZED_LOW_MIN_PCT", 75.0)
POLARIZED_MODERATE_MAX_PCT = _env_float("POLARIZED_MODERATE_MAX_PCT", 15.0)
PYRAMIDAL_LOW_MIN_PCT = _env_float("PYRAMIDAL_LOW_MIN_PCT", 70.0)
GREY_ZONE_MODERATE_MIN_PCT = _env_float("GREY_ZONE_MODERATE_MIN_PCT", 25.0)
HIGH_INTENSITY_HEAVY_MIN_PCT = _env_float("HIGH_INTENSITY_HEAVY_MIN_PCT", 25.0)

# Days of CTL/ATL/TSB history returned for display.
FITNESS_DISPLAY_DAYS = _env_int("FITNESS_DISPLAY_DAYS", 90)

# Single-leg strength sessions prescribed per week by durability risk.
STRENGTH_SESSIONS_HIGH_RISK = _env_int("STRENGTH_SESSIONS_HIGH_RISK", 3)
STRENGTH_SESSIONS_DEFAULT = _env_int("STRENGTH_SESSIONS_DEFAULT", 2)

# Plan-vs-actual compliance. Volume within [MIN, MAX] percent of the target is
# spot on; above MAX is overreach, below MIN underreach.
COMPLIANCE_SPOT_ON_MIN_PCT = _env_float("COMPLIANCE_SPOT_ON_MIN_PCT", 88.0)
COMPLIANCE_SPOT_ON_MAX_PCT = _env_float("COMPLIANCE_SPOT_ON_MAX_PCT", 112.0)
COMPLIANCE_SCORE_FULL = _env_int("COMPLIANCE_SCORE_FULL", 100)
COMPLIANCE_SCORE_COMPLETED = _env_int("COMPLIANCE_SCORE_COMPLETED", 90)
COMPLIANCE_SCORE_UNPLANNED = _env_int("COMPLIANCE_SCORE_UNPLANNED", 80)
COMPLIANCE_SCORE_OTHER = _env_int("COMPLIANCE_SCORE_OTHER", 70)
COMPLIANCE_SCORE_DEVIATION_FLOOR = _env_int("COMPLIANCE_SCORE_DEVIATION_FLOOR", 40)
COMPLIANCE_SCORE_REST_BROKEN_SHORT = _env_int("COMPLIANCE_SCORE_REST_BROKEN_SHORT", 50)
COMPLIANCE_SCORE_REST_BROKEN_LONG = _env_int("COMPLIANCE_SCORE_REST_BROKEN_LONG", 30)
COMPLIANCE_REST_BROKEN_SHORT_MIN = _env_float("COMPLIANCE_REST_BROKEN_SHORT_MIN", 30.0)

# Gear wear. Bikes have no cushioning to wear out, so their threshold is a
# service reminder; gear enters "warning" at WARNING_FRACTION of its threshold.
GEAR_BIKE_ALERT_KM = _env_float("GEAR_BIKE_ALERT_KM", 5000.0)
GEAR_WARNING_FRACTION = _env_float("GEAR_WARNING_FRACTION", 0.85)

# Weather pace slowdown: (lower bound exclusive, slowdown fraction), hottest /
# windiest first. Wind's lowest band is inclusive of its bound.
WEATHER_HEAT_BANDS = (
    (_env_float("WEATHER_HEAT_EXTREME_C", 35.0), _env_float("WEATHER_HEAT_EXTREME_PCT", 0.12)),
    (_env_float("WEATHER_HEAT_HIGH_C", 30.0), _env_float("WEATHER_HEAT_HIGH_PCT", 0.085)),
    (_env_float("WEATHER_HEAT_MODERATE_C", 25.0), _env_float("WEATHER_HEAT_MODERATE_PCT", 0.055)),
    (_env_float("WEATHER_HEAT_MILD_C", 20.0), _env_float("WEATHER_HEAT_MILD_PCT", 0.03)),
    (_env_float("WEATHER_HEAT_SLIGHT_C", 15.0), _env_float("WEATHER_HEAT_SLIGHT_PCT", 0.01)),
)
WEATHER_WIND_BANDS = (
    (_env_float("WEATHER_WIND_GALE_KMH", 35.0), _env_float("WEATHER_WIND_GALE_PCT", 0.05)),
    (_env_float("WEATHER_WIND_MODERATE_KMH", 25.0), _env_float("WEATHER_WIND_MODERATE_PCT", 0.03)),
    (_env_float("WEATHER_WIND_BREEZY_KMH", 18.0), _env_float("WEATHER_WIND_BREEZY_PCT", 0.015)),
)


# --- API & coach tunables ---
# Mutating /api routes (everything but GET and the webhooks) require this header.
# A browser cannot attach a custom header cross-origin without a CORS preflight,
# which ALLOWED_ORIGINS refuses, so the header doubles as CSRF protection.
API_CLIENT_HEADER = "X-StaminAI-Client"
API_CLIENT_HEADER_VALUE = os.getenv("API_CLIENT_HEADER_VALUE", "web").strip()
# Optional shared secret. When set, mutating routes also require
# `Authorization: Bearer <API_AUTH_TOKEN>`.
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()
# The shipped default of STRAVA_WEBHOOK_VERIFY_TOKEN. A handshake answered with
# it would let anyone who read the source register a subscription, so it is refused.
STRAVA_WEBHOOK_VERIFY_TOKEN_PLACEHOLDER = "STRAVA_WEBHOOK_SECRET"

# Request & transcript size limits (characters)
CHAT_MESSAGE_MAX_CHARS = _env_int("CHAT_MESSAGE_MAX_CHARS", 4000)
CHAT_WEEK_CONTEXT_MAX_CHARS = _env_int("CHAT_WEEK_CONTEXT_MAX_CHARS", 100000)  # serialized JSON
ATHLETE_NOTES_MAX_CHARS = _env_int("ATHLETE_NOTES_MAX_CHARS", 4000)
COACH_STORED_MESSAGE_MAX_CHARS = _env_int("COACH_STORED_MESSAGE_MAX_CHARS", 8000)
COACH_EXTRACT_MAX_MESSAGES = _env_int("COACH_EXTRACT_MAX_MESSAGES", 40)
COACH_EXTRACT_MAX_CHARS = _env_int("COACH_EXTRACT_MAX_CHARS", 20000)
# Garmin weeks a single get_health_metrics tool call may fetch live.
COACH_HEALTH_MAX_FETCH = _env_int("COACH_HEALTH_MAX_FETCH", 4)
# Planned-workout sheet reads are cached this long by /api/compliance.
COMPLIANCE_PLAN_CACHE_TTL_SEC = _env_float("COMPLIANCE_PLAN_CACHE_TTL_SEC", 300.0)

# Prediction baselines used when no qualifying activity exists.
DEFAULT_5K_PACE_SEC_PER_KM = _env_float("DEFAULT_5K_PACE_SEC_PER_KM", 300.0)  # 5:00/km
DEFAULT_SWIM_100M_SEC = _env_float("DEFAULT_SWIM_100M_SEC", 105.0)            # 1:45/100m
DEFAULT_BIKE_SPEED_KMH = _env_float("DEFAULT_BIKE_SPEED_KMH", 28.0)

# Weekly readiness heuristic (ai_coach): base score adjusted by nightly sleep
# average and overnight HRV, then clamped.
READINESS_BASE_SCORE = _env_int("READINESS_BASE_SCORE", 80)
READINESS_SLEEP_LOW_H = _env_float("READINESS_SLEEP_LOW_H", 6.5)
READINESS_SLEEP_GOOD_H = _env_float("READINESS_SLEEP_GOOD_H", 7.5)
READINESS_HRV_HIGH_MS = _env_float("READINESS_HRV_HIGH_MS", 70.0)
READINESS_HRV_LOW_MS = _env_float("READINESS_HRV_LOW_MS", 50.0)
READINESS_HRV_RECOVERED_MS = _env_float("READINESS_HRV_RECOVERED_MS", 60.0)
READINESS_SLEEP_LOW_PENALTY = _env_int("READINESS_SLEEP_LOW_PENALTY", 15)
READINESS_SLEEP_GOOD_BONUS = _env_int("READINESS_SLEEP_GOOD_BONUS", 10)
READINESS_HRV_HIGH_BONUS = _env_int("READINESS_HRV_HIGH_BONUS", 10)
READINESS_HRV_LOW_PENALTY = _env_int("READINESS_HRV_LOW_PENALTY", 15)
READINESS_SCORE_MIN = _env_int("READINESS_SCORE_MIN", 30)
READINESS_SCORE_MAX = _env_int("READINESS_SCORE_MAX", 98)

# Triathlon projection: per-distance pace multipliers on the base swim pace,
# bike speed and run pace (multi-sport fatigue), and transition seconds as
# (race_pb, training) pairs.
TRI_LEG_FACTORS = {
    "super_sprint": {"swim": 1.00, "bike": 1.03, "run": 1.02, "t1_sec": (60, 75), "t2_sec": (40, 45)},
    "sprint": {"swim": 1.02, "bike": 1.00, "run": 1.05, "t1_sec": (75, 105), "t2_sec": (50, 75)},
    "olympic": {"swim": 1.05, "bike": 0.96, "run": 1.12, "t1_sec": (90, 135), "t2_sec": (60, 90)},
    "half_ironman": {"swim": 1.08, "bike": 0.91, "run": 1.22, "t1_sec": (135, 180), "t2_sec": (90, 120)},
    "full_ironman": {"swim": 1.12, "bike": 0.84, "run": 1.38, "t1_sec": (210, 270), "t2_sec": (150, 180)},
}
