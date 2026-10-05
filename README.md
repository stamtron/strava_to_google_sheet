# ⚡ StaminAI — Adaptive Endurance & Recovery Platform

> Intelligent multi-sport analytics, biometrics synchronization, and conversational AI coaching for runners, cyclists, and triathletes.

[![CI](https://github.com/stamtron/strava_to_google_sheet/actions/workflows/ci.yml/badge.svg)](https://github.com/stamtron/strava_to_google_sheet/actions/workflows/ci.yml)

**StaminAI** automatically unifies workout telemetry from **Strava** and 24/7 health biometrics from **Garmin Connect** (Sleep, Resting HR, overnight HRV, Body Battery) into structured coaching spreadsheets in **Google Sheets**, while providing a modern glassmorphic **FastAPI + Chart.js web dashboard** and a conversational **StaminAI Coach (Gemini)** with sports-science durability tools, weather-aware pacing, grounded web search, and persistent memory.

---

## 🌟 Key Highlights

- **Multi-Sport Telemetry Sync**: Automated Strava OAuth2 sync with sport corrections (swimming pace `/100m`, pool distance correction, indoor trainer speed estimation).
- **24/7 Garmin Biometrics**: Automated tracking of Sleep hours, Resting Heart Rate (HRrest), overnight HRV, and daily Body Battery.
- **Over-the-Air Workout Push**: Parses natural Greek coaching workouts and schedules structured workout steps onto Garmin Connect & Tacx for direct watch sync.
- **Sports-Science Durability Engine**: ACWR (Acute:Chronic Workload Ratio), week-over-week run ramp rate, run spacing, Foster monotony & strain, and non-impact cross-training substitutions.
- **80/20 Polarized HR Zones**: Karvonen Heart Rate Reserve 5-Zone boundaries (Z1–Z5) tracking low vs tempo trap vs high-intensity balance.
- **Weather-Adjusted Pacing**: Real-time Open-Meteo Athens weather factoring thermal penalties (>15°C) and aerodynamic wind drag into workout targets.
- **Conversational AI Coach**: Floating chat drawer powered by Gemini with 14 tools, long-term memory (SQLite/ChromaDB), web search, and server-enforced injury disclaimers.
- **Dual Sheet Layout Engine**: Seamlessly syncs to both classic single-row and modern 7-row block Google Sheets layouts with exponential backoff retry.
- **Daily Telegram Briefings**: Automated morning and evening workout briefs and interactive two-way bot (`/today`, `/tomorrow`, `/recovery`, `/stats`, `/coach`).

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

### CLI Quick Commands

```bash
uv run python main.py                     # View recent Strava activities in terminal
uv run python main.py --sheet             # Sync Strava + Garmin to Google Sheets
uv run python main.py --backfill          # One-time full Strava history import into SQLite
uv run python main.py --telegram-next-day # Dispatch tomorrow's brief to Telegram
uv run pytest                             # Run offline test suite (348+ tests)
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
│   ├── integrations/             # External APIs (Strava, Garmin, Sheets, Telegram, Weather)
│   ├── analytics/                # Data engines (Durability, ACWR, AI Coach, Compliance, Gear)
│   ├── storage/                  # Local persistence (SQLite activities & chat memory)
│   └── api/                      # FastAPI REST server & routing
├── docs/                         # Detailed modular documentation
├── web/                          # Glassmorphic frontend (HTML/CSS/JS + Chart.js)
├── tests/                        # Offline pytest test suite (348+ tests)
├── main.py                       # CLI entry point
├── server.py                     # Web server entry point
├── pyproject.toml                # Project metadata & dependencies
└── .env.example                  # Environment configuration template
```

---

## 🧪 Testing

StaminAI includes a comprehensive offline test suite with fixtures and mocking:

```bash
uv run pytest
```
