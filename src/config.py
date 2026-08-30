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

# Cache lifetimes (seconds)
ACTIVITIES_CACHE_TTL = _env_int("ACTIVITIES_CACHE_TTL", 600)
GARMIN_CACHE_TTL = _env_int("GARMIN_CACHE_TTL", 21600)  # 6h

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

# Gemini models tried in order for AI coaching.
GEMINI_MODELS = _env_list("GEMINI_MODELS", ["gemini-2.5-flash", "gemini-2.0-flash"])

# Sport type groupings
RUN_SPORTS = ("Run", "TrailRun")
BIKE_SPORTS = ("Ride", "VirtualRide", "GravelRide", "MountainBikeRide")
SWIM_SPORTS = ("Swim",)
STRENGTH_SPORTS = ("WeightTraining", "Workout")

# Athlete Verified Personal Bests (PBs)
ATHLETE_PB_SPRINT_TRI_SEC = _env_int("ATHLETE_PB_SPRINT_TRI_SEC", 4532)   # 1h 15m 32s (Lake Doxa)
ATHLETE_PB_OLYMPIC_TRI_SEC = _env_int("ATHLETE_PB_OLYMPIC_TRI_SEC", 8647) # 2h 24m 07s (Messolonghi)
ATHLETE_PB_10K_SEC = _env_int("ATHLETE_PB_10K_SEC", 3015)                # 50m 15s (Kapodistrias)
ATHLETE_PB_5K_SEC = _env_int("ATHLETE_PB_5K_SEC", 1450)                  # 24m 10s (~4:50/km)
