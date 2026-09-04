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

# Gemini models tried in order for AI coaching. Google retires model IDs on a
# rolling basis (gemini-2.0-flash now 404s), so the list is a fallback chain and
# the newest generation goes first.
GEMINI_MODELS = _env_list("GEMINI_MODELS", ["gemini-3.6-flash", "gemini-2.5-flash"])

# Conversational coach (POST /api/ai/chat).
# A separate model list from GEMINI_MODELS: the chat agent needs function
# calling, so a model that can serve the one-shot weekly panel is not
# automatically a valid choice here.
COACH_CHAT_MODELS = _env_list("COACH_CHAT_MODELS", ["gemini-3.6-flash", "gemini-2.5-flash"])
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
AUTO_SYNC_SHEET_ON_WEBHOOK = _env_bool("AUTO_SYNC_SHEET_ON_WEBHOOK", False)

# Telegram Notifications & Next-Day Workout Dispatcher
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

