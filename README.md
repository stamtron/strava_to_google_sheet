# 🏃 Strava & Garmin to Google Sheet

Automatically fetch your workout logs from **Strava** and 24/7 health biometrics from **Garmin Connect** (Sleep, Resting HR, HRV), syncing them into your **Google Sheet** coaching log — organized by date, formatted in Greek, with all key metrics.

Includes a **FastAPI + Single Page Web App** featuring **Relative Effort (Suffer Score)** tracking, **Acute:Chronic Workload Ratio (ACWR)**, **Multi-Week Progression Charts**, a **Run Durability & Cross-Training engine**, and a **conversational AI Endurance Coach (Gemini)** with tools, web search, and persistent memory.

---

## 📁 Clean Repository Structure

```
strava_to_google_sheet/
├── src/                          # Core backend package
│   ├── config.py                 # Centralized configuration & environment variables
│   ├── formatting.py             # Duration/pace formatting & sport data corrections
│   ├── integrations/             # External service APIs
│   │   ├── strava.py             # Strava OAuth2 & activity fetcher
│   │   ├── strava_backfill.py    # Paginated full-history import + incremental sync
│   │   ├── garmin.py             # Garmin Connect authentication & biometrics
│   │   ├── sheets.py             # Google Sheets API & dual-layout sync engine
│   │   └── weather.py            # Open-Meteo daily weather integration & caching
│   ├── analytics/                # Data processing & AI
│   │   ├── metrics.py            # Relative Effort (Suffer Score), ACWR, weekly/monthly volume
│   │   ├── durability.py         # Run ramp rate, spacing, monotony/strain, cross-training
│   │   ├── ai_coach.py           # Gemini LLM coach & Peter Riegel race predictor
│   │   ├── coach_agent.py        # Conversational coach: tools, sessions, fact extraction
│   │   └── coach_memory.py       # ChromaDB long-term memory (Gemini embeddings)
│   ├── storage/                  # Local persistence (SQLite, stdlib only)
│   │   ├── activity_store.py     # Full Strava history + sync watermarks
│   │   └── chat_store.py         # Chat sessions, transcripts, keyword-recall memory
│   └── api/                      # Web API Server
│       └── server.py             # FastAPI REST endpoints & routes
├── web/                          # Frontend Single Page App
│   ├── index.html                # Dashboard + floating AI Coach chat drawer
│   ├── styles.css                # Custom glassmorphic design system
│   └── app.js                    # Chart.js charts, chat drawer & interaction logic
├── main.py                       # Root CLI entry point (incl. --backfill)
├── server.py                     # Root Web server entry point
├── tests/                        # pytest suite (offline: formatting, metrics, sheets,
│                                 #   caching, storage, backfill, durability, coach, weather)
├── pyproject.toml                # Project dependencies and config
├── README.md                     # User documentation
├── AGENTS.md                     # AI Agent Technical Manual
├── .env.example                  # Documented configuration template
├── .training_history.db          # Local SQLite history store (gitignored)
├── .coach_memory/                # ChromaDB memory store (gitignored)
├── .weather_cache.json           # Daily weather disk cache (gitignored)
└── .env
```

---

## ⚡ Features

- ✅ **Strava Workouts & Relative Effort**: OAuth2 authentication with automatic token refresh, activity metrics, and Suffer Score calculation.
- ✅ **Garmin 24/7 Health Metrics**: Automatically fetches total weekly Sleep hours, weekly average Resting Heart Rate (HRrest), overnight HRV, daily Body Battery (charged/drained), and all-day Stress from Garmin Connect.
- ✅ **80/20 Polarized Training & Zone Distribution (Z1–Z5)**: Computes 5-zone Karvonen Heart Rate Reserve thresholds and tracks weekly low (Z1-Z2) vs tempo (Z3) vs high (Z4-Z5) intensity distribution, detecting Zone 3 tempo traps.
- ✅ **Daily Weather Integration (Athens, Greece)**: Real-time historical and 7-day forecast daily weather via Open-Meteo (temperature min/max, apparent temp, rain amount/probability, wind speed, WMO condition emojis).
- ✅ **Telegram Next-Day Training Dispatcher**: Reads tomorrow's prescribed workout from Google Sheets, combines it with the Athens weather forecast and an AI coaching tip, and sends a daily brief to Telegram via Telegram Bot API.
- ✅ **Strava Real-Time Webhook Auto-Sync**: Receives incoming activity creation webhooks from Strava, updates the local SQLite store, and automatically syncs to Google Sheets.
- ✅ **Google Sheets Sync**: Dynamically supports both **Old Single-Row Layout** (rows 13–66) and **New 7-Row Block Layout** (row 67+).
- ✅ **Appends Below Coach Notes**: Preserves coach training instructions and appends Strava data under `── Strava Data ──`.
- ✅ **Weekly Totals**: Automatically sums and writes weekly totals for Running, Cycling, Swimming, Strength Training (Ενδυνάμωση), Total Training Hours, and Garmin Health Tracker (`Ύπνος __h • HRrest __ • HRV __`).
- ✅ **Swimming Pace in /100m**: Formats swimming pace in time per 100 meters (e.g. `1:24 /100μ`).
- ✅ **Swimming Distance Correction**: Halves all swimming distances and average speeds to correct watch double-counting (configurable via `SWIM_DISTANCE_DIVISOR`).
- ✅ **Indoor Cycling Estimation**: Automatically estimates distance for indoor trainer rides that report zero, based on moving time at a nominal average speed (`INDOOR_BIKE_SPEED_KMH`, default 21 km/h).
- ✅ **Interactive Web Dashboard**: Glassmorphic UI with 7-day calendar, daily weather outlook, Chart.js volume distributions, and Garmin biometrics.
- ✅ **Multi-Week & Monthly Progression Analytics**:
  - *Weekly Relative Effort & Hours Progression*
  - *Stacked Discipline Volume (Run / Bike / Swim km)*
  - *Elevation Gain Tracker (⛰️ Vertical Ascent in meters)*
  - *Acute:Chronic Workload Ratio (ACWR - Overtraining Index)* — chronic baseline is the mean of the **preceding** 4 weeks, and no ratio is reported until at least 2 weeks of history exist.
- ✅ **AI Coach & Race Predictor**: Qualitative coaching evaluation powered by **Gemini** and Peter Riegel race finish time predictions, calibrated against verified race PBs (`ATHLETE_PB_*`) with a dual PB / training projection toggle.
- ✅ **Full History Store**: `main.py --backfill` walks every page of your Strava history into a local SQLite store, so analytics can look further back than the last fetched page. Resumable, idempotent, and rate-limit aware.
- ✅ **Run Durability Engine**: week-over-week run ramp rate against the 10% rule, consecutive-run-day spacing, long-run share of weekly volume, Foster monotony & strain, per-sport ACWR, and a `risk_level` with machine-readable signals. Built for the athlete who is strong on the bike and in the water but injury-prone running.
- ✅ **Cross-Training Substitution**: converts a desired run stimulus into bike / aqua-jog aerobic equivalents plus a single-leg strength prescription, so a flagged run week has an alternative rather than just a warning.
- ✅ **Conversational AI Coach**: a floating chat drawer backed by `POST /api/ai/chat`. Gemini function calling gives it read access to your weeks, activities, load, durability assessment, projections, Garmin recovery data, and Athens weather forecast, plus grounded web search for races/gear and YouTube lookups for exercise demos. Answers cite their sources.
- ✅ **Persistent Coach Memory**: durable facts about you (injuries, goals, preferences, equipment, constraints) are stored across sessions and recalled on later turns — explicitly via the `remember_fact` tool, or by an end-of-conversation extraction pass. Inspect and prune anything it has stored from the 🧠 panel in the drawer.
- ✅ **Non-Diagnostic Injury Scope**: pain and injury questions get general, cited, non-diagnostic guidance and a physio/sports-doctor referral. The disclaimer is appended server-side, so it does not depend on the model complying.
- ✅ **Rate-limit Aware**: Strava's 200 req / 15 min budget is respected with throttled detail fetches, `Retry-After` honouring retries, and a hard error instead of silently writing blank cells.

---

## 🤖 AI Coach & Chat

The dashboard's original AI panel is a single stateless Gemini call and still works
unchanged (`POST /api/ai/coach`, with heuristic fallbacks when no API key is set).
Alongside it, the 💬 launcher in the bottom-right opens a conversational coach.

**What it can reach.** The agent has ten tools and decides which to call:

| Tool | What it answers |
| --- | --- |
| `get_week_summary` | "How was this week?" |
| `get_activities` | "What did I run last month?" |
| `get_training_load` | Total and per-sport ACWR |
| `get_run_durability` | "Is my running load risky right now?" |
| `get_race_projections` | Race and triathlon finish projections, PB or training mode |
| `get_health_metrics` | Sleep, resting HR, HRV |
| `get_weather_forecast` | Daily forecast & conditions in Athens, Greece |
| `search_web` | Races to enter, gear for the conditions |
| `find_exercise_videos` | Exercise demos, scoped to YouTube |
| `remember_fact` | Files something durable about you |

Numeric tools route through the same `corrected_distance_and_speed` the sheet and
the dashboard use, so the chat can never quote a different number than the panel
next to it.

**Memory.** Conversation history persists in SQLite rather than in process memory,
so the dev server's auto-reload cannot wipe a conversation mid-thread. Durable
facts live in a separate store selected by `COACH_MEMORY_BACKEND`:

- `sqlite` (default) — keyword-overlap recall, no embedding requests, no extra cost.
- `chroma` — recall by meaning via ChromaDB with Gemini embeddings; one embedding request per write and per turn.

Both back the same four-method interface, so switching backends needs no code
change. Facts are listed by `GET /api/coach/memory` and removed by
`DELETE /api/coach/memory/{id}` — both exposed in the drawer's 🧠 panel, because a
memory that persists indefinitely needs to be inspectable.

**API endpoints**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/dashboard` | Weekly metrics, progression history, ACWR, weather, HR zones & Polarized 80/20 balance |
| `GET /api/weather` | Daily historical and 7-day forecast weather (Athens, Greece) |
| `GET /api/durability` | Run durability assessment & cross-training suggestions |
| `POST /api/ai/coach` | One-shot weekly coaching panel |
| `POST /api/ai/chat` | Conversational turn: `{message, session_id?, week_context?}` |
| `GET`/`DELETE /api/coach/memory` | Inspect and prune stored facts |
| `POST /api/coach/memory/extract` | End-of-conversation fact extraction |
| `GET /api/history/status` | Stored activity count and date range |
| `POST /api/history/backfill` | Trigger a full history import |
| `POST /api/sheet/sync` | Google Sheets sync |
| `GET`/`POST /api/strava/webhook` | Strava real-time webhook handshake and automatic activity sync |
| `POST /api/notifications/telegram/next-day` | Send tomorrow's workout brief + Athens weather + coach tip to Telegram |

---

## ⚙️ Configuration

All settings live in `.env`. Only the credentials are required; everything else has
a sensible default. See [`.env.example`](.env.example) for the fully documented list.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` | — | Strava API app credentials |
| `GOOGLE_SHEET_ID` | — | Target spreadsheet; required for `--sheet` |
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | — | Garmin Connect login |
| `GEMINI_API_KEY` | — | AI coach (falls back to heuristics if unset) |
| `SERVER_HOST` | `127.0.0.1` | Dashboard bind address — loopback, as the API is unauthenticated |
| `SERVER_PORT` | `8000` | Dashboard port |
| `ALLOWED_ORIGINS` | localhost on `SERVER_PORT` | Comma-separated CORS origins |
| `STRAVA_REDIRECT_PORT` | `8123` | Local OAuth callback listener; **must differ from `SERVER_PORT`** |
| `STRAVA_DETAIL_DELAY_SEC` | `0.15` | Pause between per-activity detail requests |
| `STRAVA_MAX_RETRIES` | `3` | Retries on HTTP 429 |
| `ACTIVITIES_CACHE_TTL` | `600` | Strava activity cache lifetime (seconds) |
| `GARMIN_CACHE_TTL` | `21600` | Garmin week cache lifetime; finished weeks are kept indefinitely |
| `WEATHER_CACHE_TTL` | `10800` | Weather forecast cache lifetime (3 hours) |
| `ATHLETE_CITY` | `Athens, Greece` | Athlete home city for weather forecasts |
| `ATHLETE_LATITUDE` / `ATHLETE_LONGITUDE` | `37.9838` / `23.7275` | Athlete coordinates for Open-Meteo weather API |
| `ATHLETE_TIMEZONE` | `Europe/Athens` | Timezone for daily weather rollups |
| `HR_MAX` / `HR_REST` | `185` / `50` | Athlete physiology for the TRIMP effort estimate |
| `SWIM_DISTANCE_DIVISOR` | `2.0` | Swim distance/speed correction |
| `INDOOR_BIKE_SPEED_KMH` | `21.0` | Nominal speed for estimating indoor ride distance |
| `ACWR_CHRONIC_WEEKS` | `4` | Weeks in the chronic baseline window |
| `ACWR_MIN_CHRONIC_WEEKS` | `2` | Minimum history before a ratio is reported |
| `STRAVA_BACKFILL_PAGE_SIZE` | `200` | Activities per backfill page (Strava's cap) |
| `STRAVA_BACKFILL_MAX_PAGES` | `100` | Runaway guard, not a real limit |
| `STRAVA_BACKFILL_PAGE_DELAY_SEC` | `0.5` | Pause between backfill pages |
| `RUN_RAMP_SAFE_PCT` | `10.0` | Safe week-over-week run volume increase |
| `RUN_LONG_RUN_MAX_SHARE` | `0.40` | Max share of weekly run volume in one long run |
| `RUN_MIN_REST_DAYS` | `2` | Non-running days per week for tissue remodelling |
| `MONOTONY_WARN_THRESHOLD` | `2.0` | Foster monotony warning level |
| `STRAIN_WARN_THRESHOLD` | `1500.0` | Foster strain warning level |
| `AQUA_JOG_LOAD_FACTOR` | `0.90` | Run-equivalent stimulus per minute of aqua jogging |
| `BIKE_RUN_LOAD_FACTOR` | `0.55` | Run-equivalent stimulus per minute of cycling |
| `GEMINI_MODELS` | `gemini-3.6-flash,gemini-2.5-flash` | Models tried in order for the one-shot panel |
| `COACH_CHAT_MODELS` | `gemini-3.6-flash,gemini-2.5-flash` | Chat models; must support function calling |
| `COACH_MAX_TOOL_CALLS` | `8` | Ceiling on the tool loop for a single turn |
| `COACH_SESSION_TTL` | `604800` | Idle conversation lifetime (seconds, 7 days) |
| `COACH_MAX_HISTORY_MESSAGES` | `24` | Messages replayed as history each turn |
| `COACH_MEMORY_BACKEND` | `sqlite` | `sqlite` (keyword) or `chroma` (semantic) |
| `COACH_MEMORY_TOP_K` | `5` | Facts recalled and injected per turn |
| `COACH_EMBEDDING_MODEL` | `gemini-embedding-001` | Embeddings for the Chroma backend |
| `COACH_MEMORY_COLLECTION` | `athlete_memory` | ChromaDB collection name |
| `COACH_AUTO_FACT_LIMIT` | `5` | Max facts one extraction pass may store |
| `ATHLETE_PB_HALF_MARATHON_SEC` | `6415` | Verified half PB — 1h 46m 55s |
| `ATHLETE_PB_10K_SEC` | `3015` | Verified 10K PB — 50m 15s |
| `ATHLETE_PB_5K_SEC` | `1395` | Verified 5K PB — 23m 15s; run baseline in PB mode |
| `ATHLETE_PB_SPRINT_TRI_SEC` | `4532` | Verified sprint triathlon PB — 1h 15m 32s |
| `ATHLETE_PB_OLYMPIC_TRI_SEC` | `8647` | Verified olympic triathlon PB — 2h 24m 07s |
| `ATHLETE_PB_AQUATHLON_SEC` | `2234` | Verified aquathlon PB — 37m 14s |
| `ATHLETE_RACE_SWIM_100M_SEC` | `100.0` | Race-day swim baseline for PB-mode projections |
| `ATHLETE_RACE_BIKE_SPEED_KMH` | `32.5` | Race-day bike baseline for PB-mode projections |

The `ATHLETE_PB_*` values are **official race results, not training bests** — they
calibrate the PB-mode projections and are quoted in the coach's athlete profile, so
a stale value misleads both. Defaults are this repo owner's results; override them
with your own.

---

## 🚀 Prerequisites & Quick Start

### Prerequisites
- **Python 3.11+**
- **[uv](https://github.com/astral-sh/uv)** (fast Python package and project manager)
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Or via Homebrew
  brew install uv
  ```

### Step-by-Step Execution

```bash
# 1. Clone the repository
git clone https://github.com/stamtron/strava_to_google_sheet.git
cd strava_to_google_sheet

# 2. Install dependencies with uv (creates virtual environment automatically)
uv sync

# 3. Configure credentials in .env (see Setup below)
cp .env.example .env
# Edit .env with your credentials (STRAVA_*, GARMIN_*, GEMINI_API_KEY)

# 4. CLI Execution
uv run python main.py                     # Print recent Strava activities in terminal
uv run python main.py --sheet             # Sync Strava + Garmin to Google Sheet
uv run python main.py --sheet --count 50  # Fetch last 50 activities
uv run python main.py --backfill          # Import the FULL Strava history into local SQLite (run once)
uv run python main.py --backfill --no-resume  # Restart full import from page 1

# 5. Launch Web Dashboard Server
uv run python server.py                   # Open http://127.0.0.1:8000 in your browser

# 6. Run the test suite (100% offline, 275+ tests)
uv run pytest
```

---

## 🛠️ Setup

### 1. Strava API
1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Set the **Authorized Callback Domain** to `localhost`. Strava validates the
   domain only, so the callback listener's port (`STRAVA_REDIRECT_PORT`, default
   `8123`) needs no separate registration — it just has to differ from
   `SERVER_PORT` so the dashboard and the OAuth listener don't collide.
3. Copy **Client ID** and **Client Secret** into `.env`.

### 2. Google Sheets API
1. Go to [Google Cloud Console](https://console.cloud.google.com).
2. Enable the **Google Sheets API**.
3. Create **OAuth Client ID** (Desktop app), download JSON as `credentials.json` in the root folder.

### 3. Garmin Connect
1. Add your Garmin Connect email and password to `.env`:
   ```env
   GARMIN_EMAIL=your_garmin_email@example.com
   GARMIN_PASSWORD=your_password
   ```

### 4. Gemini API Key (Optional - for AI Coach)
1. Get a free API key from [aistudio.google.com](https://aistudio.google.com/).
2. Add your key to `.env`:
   ```env
   GEMINI_API_KEY=your_gemini_api_key
   ```
3. Google retires model IDs on a rolling basis. If the coach reports
   `404 ... is no longer available`, set a current model without touching code:
   ```env
   GEMINI_MODELS=gemini-3.6-flash,gemini-2.5-flash
   COACH_CHAT_MODELS=gemini-3.6-flash,gemini-2.5-flash
   ```
   The lists are fallback chains, tried left to right; the error message names
   every model that failed and what it said.

---

## 📊 Sheet Layout Detection

*   **Old Layout (Rows 13–66):** 1 row per week. Total metrics written to Column A; daily workouts appended to Columns B–H.
*   **New Layout (Row 67+):** 7-row block per week. Daily workouts appended to Row $R+4$ (`ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ`); weekly summary totals written to Row $R+5$ (`ΕΒΔΟΜΑΔΑ`).
