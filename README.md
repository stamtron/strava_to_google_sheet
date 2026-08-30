# 🏃 Strava & Garmin to Google Sheet

Automatically fetch your workout logs from **Strava** and 24/7 health biometrics from **Garmin Connect** (Sleep, Resting HR, HRV), syncing them into your **Google Sheet** coaching log — organized by date, formatted in Greek, with all key metrics.

Includes a **FastAPI + Single Page Web App** featuring **Relative Effort (Suffer Score)** tracking, **Acute:Chronic Workload Ratio (ACWR)**, **Multi-Week Progression Charts**, and an **AI Endurance Coach (Gemini)**.

---

## 📁 Clean Repository Structure

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
│       └── server.py             # FastAPI REST endpoints & routes
├── web/                          # Frontend Single Page App
│   ├── index.html                # Dashboard with Progression Analytics
│   ├── styles.css                # Custom glassmorphic design system
│   └── app.js                    # Chart.js charts & dynamic interaction logic
├── main.py                       # Root CLI entry point
├── server.py                     # Root Web server entry point
├── tests/                        # pytest suite (formatting, metrics, sheets, caching)
├── pyproject.toml                # Project dependencies and config
├── README.md                     # User documentation
├── AGENTS.md                     # AI Agent Technical Manual
├── .env.example                  # Documented configuration template
└── .env
```

---

## ⚡ Features

- ✅ **Strava Workouts & Relative Effort**: OAuth2 authentication with automatic token refresh, activity metrics, and Suffer Score calculation.
- ✅ **Garmin 24/7 Health Metrics**: Automatically fetches total weekly Sleep hours, weekly average Resting Heart Rate (HRrest), and overnight HRV from Garmin Connect.
- ✅ **Google Sheets Sync**: Dynamically supports both **Old Single-Row Layout** (rows 13–66) and **New 7-Row Block Layout** (row 67+).
- ✅ **Appends Below Coach Notes**: Preserves coach training instructions and appends Strava data under `── Strava Data ──`.
- ✅ **Weekly Totals**: Automatically sums and writes weekly totals for Running, Cycling, Swimming, Strength Training (Ενδυνάμωση), Total Training Hours, and Garmin Health Tracker (`Ύπνος __h • HRrest __ • HRV __`).
- ✅ **Swimming Pace in /100m**: Formats swimming pace in time per 100 meters (e.g. `1:24 /100μ`).
- ✅ **Swimming Distance Correction**: Halves all swimming distances and average speeds to correct watch double-counting (configurable via `SWIM_DISTANCE_DIVISOR`).
- ✅ **Indoor Cycling Estimation**: Automatically estimates distance for indoor trainer rides that report zero, based on moving time at a nominal average speed (`INDOOR_BIKE_SPEED_KMH`, default 21 km/h).
- ✅ **Interactive Web Dashboard**: Glassmorphic UI with 7-day calendar, Chart.js volume distributions, and Garmin biometrics.
- ✅ **Multi-Week & Monthly Progression Analytics**:
  - *Weekly Relative Effort & Hours Progression*
  - *Stacked Discipline Volume (Run / Bike / Swim km)*
  - *Elevation Gain Tracker (⛰️ Vertical Ascent in meters)*
  - *Acute:Chronic Workload Ratio (ACWR - Overtraining Index)* — chronic baseline is the mean of the **preceding** 4 weeks, and no ratio is reported until at least 2 weeks of history exist.
- ✅ **AI Coach & Race Predictor**: Qualitative coaching evaluation powered by **Gemini** and Peter Riegel race finish time predictions.
- ✅ **Rate-limit Aware**: Strava's 200 req / 15 min budget is respected with throttled detail fetches, `Retry-After` honouring retries, and a hard error instead of silently writing blank cells.

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
| `HR_MAX` / `HR_REST` | `185` / `50` | Athlete physiology for the TRIMP effort estimate |
| `SWIM_DISTANCE_DIVISOR` | `2.0` | Swim distance/speed correction |
| `INDOOR_BIKE_SPEED_KMH` | `21.0` | Nominal speed for estimating indoor ride distance |
| `ACWR_CHRONIC_WEEKS` | `4` | Weeks in the chronic baseline window |
| `ACWR_MIN_CHRONIC_WEEKS` | `2` | Minimum history before a ratio is reported |
| `GEMINI_MODELS` | `gemini-2.5-flash,gemini-2.0-flash` | Models tried in order |

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/stamtron/strava_to_google_sheet.git
cd strava_to_google_sheet

# 2. Install dependencies with uv
uv sync

# 3. Configure credentials in .env (see Setup below)
cp .env.example .env

# 4. CLI Execution
uv run python main.py              # Print Strava activities in terminal
uv run python main.py --sheet      # Sync Strava + Garmin to Google Sheet
uv run python main.py --sheet --count 50   # Fetch last 50 activities

# 5. Launch Web Dashboard
uv run python server.py            # Open http://127.0.0.1:8000

# 6. Run the test suite
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

---

## 📊 Sheet Layout Detection

*   **Old Layout (Rows 13–66):** 1 row per week. Total metrics written to Column A; daily workouts appended to Columns B–H.
*   **New Layout (Row 67+):** 7-row block per week. Daily workouts appended to Row $R+4$ (`ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ`); weekly summary totals written to Row $R+5$ (`ΕΒΔΟΜΑΔΑ`).
