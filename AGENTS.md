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
│   ├── formatting.py             # Duration/pace formatting & sport data corrections
│   ├── integrations/             # External service APIs
│   │   ├── strava.py             # Strava OAuth2 & activity fetcher
│   │   ├── garmin.py             # Garmin Connect authentication & biometrics
│   │   └── sheets.py             # Google Sheets API & dual-layout sync engine
│   ├── analytics/                # Data processing & AI
│   │   ├── metrics.py            # Relative Effort (Suffer Score), ACWR, weekly/monthly volume
│   │   └── ai_coach.py           # Gemini LLM coach & Peter Riegel race predictor
│   └── api/                      # Web API Server
│       └── server.py             # FastAPI REST endpoints & static routes
├── web/                          # Frontend Single Page App
│   ├── index.html                # Dashboard with Progression Analytics
│   ├── styles.css                # Custom glassmorphic design system
│   └── app.js                    # Chart.js charts & dynamic interaction logic
├── tests/                        # pytest suite
│   ├── test_formatting.py        # Unit conversions & sport corrections
│   ├── test_metrics.py           # Relative effort, weekly rollups, ACWR
│   ├── test_sheets.py            # Date-range parsing, weekly totals, cell formatting
│   └── test_caching.py           # Activity-cache and Garmin week-cache correctness
├── main.py                       # Root CLI entry point
├── server.py                     # Root Web server entry point
├── pyproject.toml                # Project configuration (deps, pytest)
├── README.md                     # Documentation
├── .env.example                  # Documented configuration template
└── .env
```

---

## 🔑 Key Backend Components

1. **[`src/config.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/config.py)**
   - Centralizes project constants, sheet names, token paths, and `.env` loading.
   - All tunables are env-overridable through `_env_int` / `_env_float` / `_env_list`
     helpers that fall back to the default on malformed input. Anything a reviewer
     would call a magic number (HR max/rest, the swim divisor, the indoor-bike
     speed, ACWR window sizes, cache TTLs, Gemini model names) lives here, not
     inline at the call site. See [`.env.example`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/.env.example)
     for the documented list.
   - `SERVER_HOST` defaults to `127.0.0.1` and `ALLOWED_ORIGINS` to localhost only:
     the API is unauthenticated, so it must not be exposed to the LAN by default.
   - `STRAVA_REDIRECT_PORT` (8123) is deliberately distinct from `SERVER_PORT`
     (8000) — the OAuth callback listener blocks its thread, so sharing the
     dashboard's port deadlocks the flow.

2. **[`src/formatting.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/formatting.py)**
   - Single home for duration and pace formatting, in both English (`format_duration`,
     `format_pace`) and Greek (`format_duration_el`, `format_duration_short_el`,
     `format_pace(..., greek=True)`). Do not reintroduce local copies in `main.py`
     or `sheets.py`.
   - `corrected_distance_and_speed(act)` is the **only** place sport corrections
     are applied: the swim divisor and the indoor-trainer distance estimate. Every
     consumer (CLI, sheets, metrics, AI coach) routes through it so the numbers
     agree everywhere.
   - `is_indoor_ride(act)` treats a ride as indoor only when it is trainer-flagged
     *and* reports ~zero distance; a trainer that does report distance keeps it.

3. **[`src/integrations/strava.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/strava.py)**
   - Manages Strava OAuth2 browser authorization, token caching in `token.json`, and automatic token refreshes.
   - Fetches activity summaries (`fetch_activities`) and detailed metrics (`fetch_activity_detail`).
   - Raises `StravaAuthRequired` and `StravaRateLimitError` rather than returning
     `{}` — a swallowed 429 previously wrote blank cells over real sheet data.
   - `fetch_details_for_activities` throttles by `STRAVA_DETAIL_DELAY_SEC` and
     retries 429s up to `STRAVA_MAX_RETRIES`, honouring `Retry-After`.

4. **[`src/integrations/garmin.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/garmin.py)**
   - Connects to Garmin Connect using credentials from `.env` (`GARMIN_EMAIL`, `GARMIN_PASSWORD`).
   - Caches session tokens in `.garmin_tokens/` to prevent repeated logins.
   - Queries daily sleep seconds, resting heart rate (RHR), and overnight HRV for any week range.
   - `get_weekly_health_summaries` batches many weeks behind **one** login and
     persists results to `.garmin_cache.json`. Finished weeks are cached
     indefinitely; the in-progress week honours `GARMIN_CACHE_TTL`. Prefer it over
     per-week calls — the dashboard used to issue ~210 sequential requests.

5. **[`src/integrations/sheets.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/sheets.py)**
   - Authenticates via desktop OAuth2 (`credentials.json` -> `gsheets_token.json`).
   - Dynamic layout detection: batch inspects $A(R+4)$ for `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ` to distinguish **New Block Layout** from **Old Single-Row Layout**.
   - Formats swimming pace in `/100μ` (distance corrected via `src.formatting`), running pace in `/χλμ`, cycling in `χλμ/ω`.
   - Populates weekly sport totals and Garmin health tracker in Column A (Old) or Column B of `ΕΒΔΟΜΑΔΑ` (New).
   - Detail lookups accept both int and string activity ids, since cached details
     round-trip through JSON.

6. **[`src/analytics/metrics.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/metrics.py)**
   - Extracts Strava Relative Effort (`suffer_score`) or computes HR-based TRIMP stress from `HR_MAX`/`HR_REST`.
   - Computes **ACWR** as the current week's load over the mean of the
     `ACWR_CHRONIC_WEEKS` weeks *preceding* it. The current week is excluded from
     its own baseline; with fewer than `ACWR_MIN_CHRONIC_WEEKS` of history, or a
     zero baseline, `acwr_ratio` is `None` and `zone` is `"unknown"`.
   - Emits machine-readable `zone` strings (`low`/`optimal`/`overreaching`/`spike`/
     `unknown`) — no colours or UI text. Presentation belongs in `web/app.js`.
   - `build_progression_history(weeks, acwr_map=...)` accepts a precomputed ACWR
     map so callers don't recompute it per request.

7. **[`src/analytics/ai_coach.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/ai_coach.py)**
   - Generates qualitative coach feedback and readiness scoring, trying each entry of `GEMINI_MODELS` in order, with sports-science heuristic fallbacks.
   - Computes Peter Riegel race finish time predictions for 5K, 10K, 21.1K, and 42.2K.

8. **[`src/api/server.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/api/server.py)**
   - FastAPI backend providing `GET /api/dashboard`, `POST /api/ai/coach`, `POST /api/sheet/sync`.
   - Local activity caching in `.activities_cache.json`, gated by `_cache_satisfies`,
     which checks the TTL **and** that the cache holds enough activities for the
     requested `count` (a 20-activity cache must not answer a 50 request).
   - Mounted by `server.py` at the repo root, which only calls `uvicorn.run` with
     `SERVER_HOST`/`SERVER_PORT`.

---

## 🛠️ Development & Execution Commands

```bash
# Install dependencies (including the dev group)
uv sync

# Run CLI tool
uv run python main.py --count 30

# Sync to Google Sheets via CLI
uv run python main.py --sheet --count 30

# Run Web Dashboard Server
uv run python server.py

# Run the test suite
uv run pytest
uv run pytest tests/test_metrics.py -k acwr -v

# Type / compile check
uv run python -m py_compile main.py server.py src/config.py src/formatting.py src/integrations/*.py src/analytics/*.py src/api/*.py
```

Note: bare `python` is not on `PATH` in this environment — use `uv run python` or `python3`.

---

## 🧪 Testing Conventions

- Tests live in `tests/` and import the project as `src.*`; `pyproject.toml` sets
  `pythonpath = ["."]` so the uninstalled package resolves from the repo root.
- Everything under test is pure: formatting, corrections, weekly rollups, ACWR,
  date parsing, and the cache-freshness predicates. No test performs network I/O
  or touches Strava, Garmin, Google, or Gemini.
- When changing a tunable's default in `config.py`, prefer asserting against the
  imported constant (as the existing tests do) rather than hardcoding the number,
  so the suite tracks configuration instead of duplicating it.

