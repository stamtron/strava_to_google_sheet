# 🏃 Strava & Garmin to Google Sheet

Automatically fetch your workout logs from **Strava** and 24/7 health biometrics from **Garmin Connect** (Sleep, Resting HR, HRV), syncing them into your **Google Sheet** coaching log — organized by date, formatted in Greek, with all key metrics.

Includes a **FastAPI + Single Page Web App** featuring **Relative Effort (Suffer Score)** tracking, **Acute:Chronic Workload Ratio (ACWR)**, **Multi-Week Progression Charts**, and an **AI Endurance Coach (Gemini)**.

---

## 📁 Clean Repository Structure

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
│       └── server.py             # FastAPI REST endpoints & routes
├── web/                          # Frontend Single Page App
│   ├── index.html                # Dashboard with Progression Analytics
│   ├── styles.css                # Custom glassmorphic design system
│   └── app.js                    # Chart.js charts & dynamic interaction logic
├── main.py                       # Root CLI entry point
├── server.py                     # Root Web server entry point
├── pyproject.toml                # Project dependencies and config
├── README.md                     # User documentation
├── AGENTS.md                     # AI Agent Technical Manual
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
- ✅ **Swimming Distance Correction**: Halves all swimming distances and average speeds (divided by 2) to correct watch double-counting.
- ✅ **Indoor Cycling Estimation**: Automatically estimates distance for indoor trainer rides based on moving time at a 21 km/h average speed.
- ✅ **Interactive Web Dashboard**: Glassmorphic UI with 7-day calendar, Chart.js volume distributions, and Garmin biometrics.
- ✅ **Multi-Week & Monthly Progression Analytics**:
  - *Weekly Relative Effort & Hours Progression*
  - *Stacked Discipline Volume (Run / Bike / Swim km)*
  - *Elevation Gain Tracker (⛰️ Vertical Ascent in meters)*
  - *Acute:Chronic Workload Ratio (ACWR - Overtraining Index)*
- ✅ **AI Coach & Race Predictor**: Qualitative coaching evaluation powered by **Gemini 2.5 Flash** and Peter Riegel race finish time predictions.

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
uv run python server.py            # Open http://localhost:8000
```

---

## 🛠️ Setup

### 1. Strava API
1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an application with `localhost` callback domain.
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
