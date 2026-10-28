# ⚡ StaminAI — Adaptive Endurance & Recovery Platform

> Intelligent multi-sport analytics, biometrics synchronization, and conversational AI coaching for runners, cyclists, and triathletes.

[![CI](https://github.com/stamtron/strava_to_google_sheet/actions/workflows/ci.yml/badge.svg)](https://github.com/stamtron/strava_to_google_sheet/actions/workflows/ci.yml)

**StaminAI** automatically unifies workout telemetry from **Strava** and 24/7 health biometrics from **Garmin Connect** (Sleep, Resting HR, overnight HRV, Body Battery) into structured coaching spreadsheets in **Google Sheets**, while providing a modern glassmorphic **FastAPI + Chart.js web dashboard** and a conversational **StaminAI Coach (Gemini)** with sports-science durability tools, weather-aware pacing, grounded web search, and persistent memory.

---

## 🌟 Key Highlights

- **Multi-Sport Telemetry Sync**: Automated Strava OAuth2 sync with sport corrections (swimming pace `/100m`, pool distance correction, indoor trainer speed estimation).
- **24/7 Garmin Biometrics & Baselines**: Automated tracking of Sleep hours, Resting Heart Rate (HRrest), overnight HRV, daily Body Battery, plus statistical baseline normal ranges ($\mu \pm 0.75\sigma$) with autonomic fatigue anomaly detection.
- **Fitness, Fatigue & Form (Banister EWMA)**: Chronic Training Load (CTL, 42d), Acute Training Load (ATL, 7d), and Training Stress Balance (TSB = CTL - ATL) tracking readiness, freshness, and optimal overload.
- **Equipment & Shoe Mileage Tracker**: Automated tracking of cumulative mileage per shoe/bike with wear degradation thresholds and retirement alert warnings.
- **Race-Day Taper & Nutrition Protocols**: Scientific 3-week step-down volume progression, carbohydrate-loading, pre-race fueling schedules, and ambient-scaled electrolyte targets.
- **Over-the-Air Workout Push**: Parses natural Greek coaching workouts and schedules structured workout steps onto Garmin Connect & Tacx for direct watch sync.
- **Sports-Science Durability Engine**: ACWR (Acute:Chronic Workload Ratio), week-over-week run ramp rate, run spacing, Foster monotony & strain, and non-impact cross-training substitutions.
- **80/20 Polarized HR Zones**: Karvonen Heart Rate Reserve 5-Zone boundaries (Z1–Z5) tracking low vs tempo trap vs high-intensity balance.
- **Weather-Adjusted Pacing**: Real-time Open-Meteo Athens weather factoring thermal penalties (>15°C) and aerodynamic wind drag into workout targets.
- **Conversational AI Coach**: Floating chat drawer powered by Gemini with 14 tools, long-term memory (SQLite/ChromaDB), web search, and server-enforced injury disclaimers.
- **Dual Sheet Layout Engine**: Seamlessly syncs to both classic single-row and modern 7-row block Google Sheets layouts with exponential backoff retry.
- **Daily & Weekly Telegram Briefings**: Automated morning/evening briefs, Sunday executive weekly digests (`/weekly`, `/form`), and interactive two-way bot (`/today`, `/tomorrow`, `/recovery`, `/stats`, `/gear`, `/coach`).

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.11+**
- **[uv](https://github.com/astral-sh/uv)** (fast Python package manager)

```bash
# 1. Clone the repository
git clone https://github.com/stamtron/strava_to_google_sheet.git
cd strava_to_google_sheet

# 2. Install dependencies (creates virtual environment automatically)
uv sync

# 3. Configure credentials in .env
cp .env.example .env
# Edit .env with your STRAVA_*, GARMIN_*, and optional GEMINI_API_KEY credentials

# 4. Launch the Web Dashboard
uv run python server.py
# Open http://127.0.0.1:8000 in your browser
```

> 🔒 **Security:** the dashboard binds to `127.0.0.1` only. State-changing API calls require the
> `X-StaminAI-Client: web` header (sent automatically by the web app) and, optionally, an
> `API_AUTH_TOKEN` bearer token. The Strava and Telegram webhooks reject all requests until
> `STRAVA_WEBHOOK_SECRET_PARAM` / `STRAVA_WEBHOOK_VERIFY_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` are set.
> See [Configuration](docs/configuration.md).

### CLI Quick Commands

```bash
uv run python main.py                     # View recent Strava activities in terminal
uv run python main.py --sheet             # Sync Strava + Garmin to Google Sheets
uv run python main.py --backfill          # One-time full Strava history import into SQLite
uv run python main.py --telegram-next-day # Dispatch tomorrow's brief to Telegram
uv run python main.py --telegram-weekly   # Dispatch weekly executive summary to Telegram
uv run pytest                             # Run offline test suite (460+ tests)
uv run ruff check                         # Lint
```

---

## 📚 Documentation

Detailed documentation is organized in the [`docs/`](docs/) directory:

| Guide | Description |
| --- | --- |
| 🛠️ [**Setup & Installation Guide**](docs/setup.md) | Step-by-step API setup for Strava, Google Sheets, Garmin Connect, Gemini, and Telegram. |
| ⚙️ [**Configuration Reference**](docs/configuration.md) | Complete `.env` variables reference: physiology, thresholds, backfill, and athlete race PBs. |
| 🤖 [**StaminAI Coach & Memory**](docs/ai_coach.md) | Gemini conversational agent, 14 tools, SQLite vs Chroma memory, and injury safety boundaries. |
| 📈 [**Sports Science & Analytics**](docs/analytics.md) | ACWR, run durability, Foster monotony/strain, 80/20 polarized zones, weather pacing, and Riegel predictions. |
| 🌐 [**REST API Reference**](docs/api.md) | Complete documentation of FastAPI endpoints for dashboard, telemetry, chat, workouts, and webhooks. |
| 📊 [**Google Sheets Sync & Layouts**](docs/sheets_sync.md) | Old single-row vs New 7-row block layout detection and sport formatting rules. |

---

## 📁 Repository Overview

```
strava_to_google_sheet/
├── src/                          # Core backend package
│   ├── config.py                 # Centralized configuration & environment variables
│   ├── formatting.py             # Duration/pace formatting & sport data corrections
│   ├── status_labels.py          # Display text for analytics status keys
│   ├── timeutil.py               # Athlete-timezone clock helpers
│   ├── integrations/             # External APIs (Strava, Garmin, Sheets, Telegram, Weather)
│   ├── analytics/                # Data engines (Durability, ACWR, AI Coach, Compliance, Gear)
│   ├── storage/                  # Local persistence (SQLite activities, chat memory, atomic token files)
│   └── api/                      # FastAPI REST server & routing
├── docs/                         # Detailed modular documentation
├── web/                          # Glassmorphic frontend (HTML/CSS/JS + Chart.js)
├── tests/                        # Offline pytest test suite (460+ tests)
├── main.py                       # CLI entry point
├── server.py                     # Web server entry point
├── pyproject.toml                # Project metadata, dependencies, pytest & ruff config
└── .env.example                  # Environment configuration template
```

---

## 🧪 Testing

StaminAI includes a comprehensive offline test suite with fixtures and mocking:

```bash
uv run pytest
```
