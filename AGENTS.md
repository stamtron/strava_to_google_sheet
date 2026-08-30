# 🤖 AGENTS.md — Strava & Garmin to Google Sheet

This document serves as the primary technical guide for AI agents and developers working on the `strava_to_google_sheet` repository.

---

## 📌 Project Overview

`strava_to_google_sheet` is an automated fitness tracking integration that fetches workout activities from **Strava** and 24/7 health biometrics (Sleep, Resting Heart Rate, HRV) from **Garmin Connect**, formatting and synchronizing them into a structured Greek coaching spreadsheet in **Google Sheets**, while providing a modern **FastAPI + Chart.js** analytics web app with **AI Coaching (Gemini)** and **Acute:Chronic Workload Ratio (ACWR)** tracking.

---

## 🏗️ Architecture & Module Organization

```
strava_to_google_sheet/
├── src/                          # Core backend package
│   ├── config.py                 # Centralized configuration & environment variables
│   ├── integrations/             # External service APIs
│   │   ├── strava.py             # Strava OAuth2 & activity fetcher
│   │   ├── garmin.py             # Garmin Connect authentication & biometrics
│   │   └── sheets.py             # Google Sheets API & dual-layout sync engine
│   ├── analytics/                # Data processing & AI
│   │   ├── metrics.py            # Relative Effort (Suffer Score), ACWR, weekly/monthly volume
│   │   └── ai_coach.py           # Gemini 2.5 Flash LLM coach & Peter Riegel race predictor
│   └── api/                      # Web API Server
│       └── server.py             # FastAPI REST endpoints & static routes
├── web/                          # Frontend Single Page App
│   ├── index.html                # Dashboard with Progression Analytics
│   ├── styles.css                # Custom glassmorphic design system
│   └── app.js                    # Chart.js charts & dynamic interaction logic
├── main.py                       # Root CLI entry point
├── server.py                     # Root Web server entry point
├── pyproject.toml                # Project configuration
├── README.md                     # Documentation
└── .env
```

---

## 🔑 Key Backend Components

1. **[`src/config.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/config.py)**
   - Centralizes project constants, sheet names, token paths, and `.env` loading.

2. **[`src/integrations/strava.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/strava.py)**
   - Manages Strava OAuth2 browser authorization, token caching in `token.json`, and automatic token refreshes.
   - Fetches activity summaries (`fetch_activities`) and detailed metrics (`fetch_activity_detail`).

3. **[`src/integrations/garmin.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/garmin.py)**
   - Connects to Garmin Connect using credentials from `.env` (`GARMIN_EMAIL`, `GARMIN_PASSWORD`).
   - Caches session tokens in `.garmin_tokens/` to prevent repeated logins.
   - Queries daily sleep seconds, resting heart rate (RHR), and overnight HRV for any week range.

4. **[`src/integrations/sheets.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/sheets.py)**
   - Authenticates via desktop OAuth2 (`credentials.json` -> `gsheets_token.json`).
   - Dynamic layout detection: batch inspects $A(R+4)$ for `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ` to distinguish **New Block Layout** from **Old Single-Row Layout**.
   - Formats swimming pace in `/100μ` (with distance halved), running pace in `/χλμ`, cycling in `χλμ/ω`.
   - Populates weekly sport totals and Garmin health tracker in Column A (Old) or Column B of `ΕΒΔΟΜΑΔΑ` (New).

5. **[`src/analytics/metrics.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/metrics.py)**
   - Extracts Strava Relative Effort (`suffer_score`) or computes HR-based TRIMP stress.
   - Computes **Acute:Chronic Workload Ratio (ACWR)** comparing current 7-day load to 28-day rolling baseline.
   - Builds multi-week progression datasets for charting volume, elevation, and load.

6. **[`src/analytics/ai_coach.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/ai_coach.py)**
   - Generates qualitative coach feedback and readiness scoring using Gemini (`gemini-2.5-flash`, `gemini-3.6-flash`) with sports-science heuristic fallbacks.
   - Computes Peter Riegel race finish time predictions for 5K, 10K, 21.1K, and 42.2K.

7. **[`src/api/server.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/api/server.py)**
   - FastAPI backend providing `GET /api/dashboard`, `POST /api/ai/coach`, `POST /api/sheet/sync`.
   - Local activity caching in `.activities_cache.json` with automatic rate-limit fallbacks.

---

## 🛠️ Development & Execution Commands

```bash
# Run CLI tool
uv run python main.py --count 30

# Sync to Google Sheets via CLI
uv run python main.py --sheet --count 30

# Run Web Dashboard Server
uv run python server.py

# Type / compile check
uv run python -m py_compile main.py server.py src/config.py src/integrations/*.py src/analytics/*.py src/api/*.py
```
