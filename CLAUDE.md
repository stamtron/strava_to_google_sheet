# CLAUDE.md — StaminAI Development & Agent Guide

This file provides guidance and key instructions for Claude (and Claude Code CLI) working on the **StaminAI** (`strava_to_google_sheet`) repository.

---

## 📌 Project Overview

**StaminAI** is an adaptive multi-sport endurance training platform that unifies workout telemetry from **Strava** and 24/7 health biometrics from **Garmin Connect** (Sleep, Resting Heart Rate, overnight HRV, Body Battery), formats and synchronizes them into a structured coaching spreadsheet in **Google Sheets**, and provides a modern **FastAPI + Chart.js** analytics web app with:
- **Banister Impulse-Response EWMA** model (CTL 42d, ATL 7d, TSB = CTL - ATL)
- **Acute:Chronic Workload Ratio (ACWR)** and 80/20 polarized HR zones (Karvonen)
- **Garmin Biometric Baselines** ($\mu \pm 0.75\sigma$) with autonomic fatigue anomaly detection
- **Run Durability Engine** (ramp rate, spacing, monotony/strain, cross-training conversions)
- **Equipment & Shoe Wear Monitor** (mileage alerts and degradation thresholds)
- **Race-Day Taper & Strategy Protocols** and Peter Riegel race finish predictions
- **Over-the-Air Workout Push** to Garmin watches and the Tacx Training app
- **Telegram Bot & Dispatcher** (morning/evening briefs, Sunday executive weekly digests, `/today`, `/tomorrow`, `/weekly`, `/form`, `/recovery`, `/stats`, `/gear`, `/coach`)
- **Conversational AI Coach** (Gemini LLM) with 14 tools, grounded search, and persistent memory (SQLite/ChromaDB).

---

## 🛠️ Common Commands

All operations use **[uv](https://github.com/astral-sh/uv)**:

```bash
# Environment & Dependencies
uv sync                                    # Install dependencies into .venv
uv add <package>                           # Add new dependency

# Web Server & Dashboard
uv run python server.py                    # Launch FastAPI web dashboard on http://127.0.0.1:8000

# CLI Operations
uv run python main.py                      # Print recent Strava activities in terminal
uv run python main.py --sheet              # Sync Strava & Garmin to Google Sheets
uv run python main.py --backfill           # Full paginated Strava history backfill to SQLite
uv run python main.py --telegram-next-day  # Dispatch tomorrow's briefing to Telegram
uv run python main.py --telegram-weekly    # Dispatch weekly executive summary to Telegram

# Testing & Code Quality
uv run pytest                              # Run full test suite (460+ tests, offline)
uv run pytest tests/test_metrics.py        # Run single test module
uv run pytest -k "test_classify_tsb"       # Run specific test pattern
uv run ruff check                          # Lint codebase (config in pyproject.toml)
node --check web/app.js                    # Validate frontend JavaScript syntax
```

---

## 🏗️ Architecture & Core Modules

```
strava_to_google_sheet/
├── src/
│   ├── config.py                 # Central configuration, env tunables, path constants
│   ├── formatting.py             # Duration/pace formatters, corrected_distance_and_speed
│   ├── status_labels.py          # Display text for analytics status keys (Telegram/coach)
│   ├── timeutil.py               # Athlete-timezone now_local()/today_local()
│   ├── integrations/
│   │   ├── strava.py             # Strava OAuth2 & activity endpoints
│   │   ├── strava_backfill.py    # Paginated full-history import + resume cursor
│   │   ├── garmin.py             # Garmin Connect biometrics, sleep, HRV & baseline bands
│   │   ├── garmin_workouts.py    # Structured workout generator & OTA watch sync
│   │   ├── sheets.py             # Google Sheets API & dual-layout sync engine
│   │   ├── telegram.py           # Telegram dispatcher, daily/weekly briefs, interactive bot
│   │   └── weather.py            # Open-Meteo Athens weather & disk cache
│   ├── analytics/
│   │   ├── metrics.py            # Relative effort, ACWR, Banister CTL/ATL/TSB model, Karvonen 80/20
│   │   ├── durability.py         # Run ramp rate, spacing, monotony/strain, cross-training
│   │   ├── compliance.py         # Plan vs actual workout execution matcher
│   │   ├── gear.py               # Shoe & bike cumulative mileage tracker & alert thresholds
│   │   ├── weather_pace.py       # Temperature & wind pace slowdown adjustment
│   │   ├── ai_coach.py           # Gemini LLM qualitative coaching & Riegel predictions
│   │   ├── coach_agent.py        # Conversational agent: 14 tools, grounded search, chat session
│   │   └── coach_memory.py       # ChromaDB / SqliteMemory long-term fact store
│   ├── storage/
│   │   ├── activity_store.py     # SQLite historical activities + sync watermarks
│   │   ├── chat_store.py         # SQLite chat sessions, transcripts, SqliteMemory
│   │   └── atomic_file.py        # Atomic 0600 JSON writes (tokens, caches)
│   └── api/
│       └── server.py             # FastAPI REST endpoints & static routes
├── web/                          # Frontend SPA (HTML5, Vanilla CSS glassmorphic tokens, Chart.js)
│   ├── index.html                # Dashboard + tabs + floating coach drawer
│   ├── styles.css                # Glassmorphic dark design system
│   └── app.js                    # Interaction logic, Chart.js instances, chat client
├── tests/                        # Offline pytest suite (460+ unit tests)
├── docs/                         # Modular documentation (setup, config, analytics, coach, api, sheets)
├── main.py                       # CLI root entry point
├── server.py                     # Web server root entry point
└── pyproject.toml                # Dependencies, project metadata (staminai)
```

---

## 🔑 Critical Invariants & Rules

1. **Bare Python is NOT on PATH**:
   Always execute through `uv run pytest` or `uv run python`.
2. **Deterministic Mathematics in Analytics**:
   - The LLM coach *explains* metrics; it **never** computes them.
   - All durability, ACWR, Banister EWMA, and biometric baseline maths live in `src/analytics/` so they are 100% unit-testable offline.
3. **No Presentation Strings in Analytics**:
   Analytics functions emit machine-readable status keys only (`low`, `optimal`, `overreaching`, `spike`, `fresh`, `neutral`, `high_fatigue`, `advice_key`s). Presentation labels, colors, and emojis belong strictly in `src/status_labels.py` (Telegram/coach text) and `web/app.js` / `web/styles.css` (dashboard).
   - Analytics also return no `summary` prose; callers render it, e.g. `status_labels.compliance_summary`.
4. **Single Home for Sport Corrections**:
   `corrected_distance_and_speed(act)` in `src/formatting.py` is the **only** place where swim distance divisors and indoor bike distance estimates are applied. Never calculate ad-hoc distance adjustments inline.
5. **No Magic Numbers**:
   All physiological constants, HR max/rest, time constants, limits, thresholds, and ports live in `src/config.py` backed by `_env_*` helpers.
6. **ACWR Baseline Invariant**:
   The current week is strictly excluded from its own chronic baseline:
   `chronic_weeks = preceding_weeks[-chronic_window:]`. Weeks are calendar weeks — a week
   with no training counts as zero load, never skipped. Missing history yields
   `acwr_ratio=None` with `status` `insufficient_history` / `no_baseline`.
7. **Banister EWMA Warm-Up Invariant**:
   `calculate_fitness_fatigue` warms up EWMA starting from the earliest recorded activity in the dataset (or 2×CTL days prior to display) to prevent day-1 cold-start bias, and slices to the visible 90-day window.
8. **Frontend Cache Busting Invariant**:
   Whenever `web/styles.css` or `web/app.js` is modified, the version query parameter in `web/index.html` (e.g. `styles.css?v=X.Y`, `app.js?v=X.Y`) must be bumped.
9. **Offline Test Suite Integrity**:
   Every test in `tests/` must run offline with synthetic mock fixtures. Never make live network requests to Strava, Garmin, Google Sheets, or Telegram inside pytest.
10. **Athlete-Local Time**:
   Use `src/timeutil.today_local()` / `now_local()` (evaluated in `ATHLETE_TIMEZONE`), never `date.today()` / `datetime.now()`. Ruff's `DTZ` rules enforce this outside `tests/`.
11. **API Security (CSRF & Webhooks)**:
   - Every non-GET `/api/*` route requires the `X-StaminAI-Client: web` header (`verify_client`); when `API_AUTH_TOKEN` is set, `Authorization: Bearer <token>` too. Frontend calls go through `apiFetch` in `web/app.js`; test POSTs must send the header.
   - Webhooks fail closed: `/api/strava/webhook` needs `STRAVA_WEBHOOK_SECRET_PARAM` (and a non-placeholder `STRAVA_WEBHOOK_VERIFY_TOKEN`), `/api/notifications/telegram/webhook` needs `TELEGRAM_WEBHOOK_SECRET`. Never make an unset secret mean "allow".
   - Keep `SERVER_HOST=127.0.0.1` and `ALLOWED_ORIGINS` localhost-only. 500s return a generic message; details go to the log.
12. **Secrets on Disk**:
   Token and cache JSON files are written via `src/storage/atomic_file.write_json_atomic` (temp file + `os.replace`, mode `0o600`).
13. **Lint Clean**:
   `uv run ruff check` must pass. After `--fix`, review the diff: F811 can remove deliberate function-local imports (endpoints import `send_telegram_message` lazily so tests can monkeypatch it).
