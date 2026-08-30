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

# API Keys & Credentials
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1CaLk5B6r-o6vAj96zmYFUF55caKFCv75clbYG7Fq7cA")
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# File Paths
TOKEN_FILE = str(PROJECT_ROOT / "token.json")
CREDENTIALS_FILE = str(PROJECT_ROOT / "credentials.json")
GSHEETS_TOKEN_FILE = str(PROJECT_ROOT / "gsheets_token.json")
GARMIN_TOKEN_DIR = str(PROJECT_ROOT / ".garmin_tokens")
ACTIVITIES_CACHE_FILE = str(PROJECT_ROOT / ".activities_cache.json")
WEB_DIR = str(PROJECT_ROOT / "web")

# Google Sheets Configuration
SHEET_NAME = "Προπόνηση-Ανατροφοδότηση"
FIRST_WEEK_ROW = 13
ROWS_PER_WEEK = 1
GSHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
