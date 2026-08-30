# 🤖 AGENTS.md — Strava & Garmin to Google Sheet

This document serves as the primary technical guide for AI agents and developers working on the `strava_to_google_sheet` repository.

---

## 📌 Project Overview

`strava_to_google_sheet` is an automated fitness tracking integration that fetches workout activities from **Strava** and 24/7 health biometrics (Sleep, Resting Heart Rate, HRV) from **Garmin Connect**, formatting and synchronizing them into a structured Greek coaching spreadsheet in **Google Sheets**.

---

## 🏗️ Architecture & Component Overview

```
                   ┌──────────────┐
                   │  Strava API  │
                   └──────┬───────┘
                          │ Activities & Workouts
                          ▼
┌──────────────────┐  ┌───────────┐  ┌──────────────────┐
│  Garmin Connect  │─▶│  main.py  │◀─│  Google Sheets   │
│  (Sleep/RHR/HRV) │  └─────┬─────┘  │   OAuth2 Token   │
└──────────────────┘        │        └──────────────────┘
                            ▼
                  ┌──────────────────┐
                  │ google_sheets.py │
                  └─────────┬────────┘
                            ▼
              ┌───────────────────────────┐
              │  Google Sheets Coaching   │
              │  (Old & New Formats)      │
              └───────────────────────────┘
```

### Key Modules:

1. **[`main.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/main.py)**
   - Entry point for the CLI application.
   - CLI Flags:
     - `--count <int>`: Number of recent activities to fetch (default: 30).
     - `--sheet`: Synchronize fetched data into Google Sheets.
   - Fetches activity summaries and detailed stats (elevation, HR, suffer score, temperature, calories) from Strava.
   - Groups activities by local date and prints a formatted terminal summary.

2. **[`google_sheets.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/google_sheets.py)**
   - Authenticates with Google Sheets API via desktop OAuth2 (`credentials.json` -> `gsheets_token.json`).
   - **Date Range Parsing (`parse_date_range`)**: Normalizes dashes and quotes; parses both 2-digit years (`'25`, `'26`) and 4-digit years (`2025`, `2026`).
   - **Dynamic Layout Detection**:
     - Inspects Column A 4 rows below the week start (`A{R+4}`).
     - If it contains `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ`, handles the week using the **New Block Layout**.
     - Otherwise, handles the week using the **Old Single-Row Layout**.
   - **Daily Activity Logging**:
     - *Old Layout:* Appends Strava details under `── Strava Data ──` in Columns B–H of row $R$.
     - *New Layout:* Appends Strava details in Columns B–H of the `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ` row (row $R+4$).
     - *Swimming Pace:* Formatted as time per 100 meters (e.g. `1:24 /100μ`).
     - *Running/Walking Pace:* Formatted as time per km (e.g. `5:10 /χλμ`).
     - *Cycling Speed:* Formatted in km/h (e.g. `27.5 χλμ/ω`).
   - **Weekly Totals Calculation**:
     - Sums distance and duration for Running, Cycling, Swimming (distance halved), and Strength Training.
     - Fetches Garmin Connect health summary for the week (Sleep, Resting HR, HRV).
     - *Old Layout:* Updates Column A of row $R$, including `Ύπνος {sleep}h • HRrest {rhr} • HRV {hrv}`.
     - *New Layout:* Updates Column B of row $R+5$ (`ΕΒΔΟΜΑΔΑ`), populating placeholders in the template in-place.

3. **[`garmin_client.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/garmin_client.py)**
   - Connects to Garmin Connect using credentials from `.env` (`GARMIN_EMAIL`, `GARMIN_PASSWORD`).
   - Caches session tokens in `.garmin_tokens/` to avoid repeated logins.
   - `get_weekly_health_summary(start_date, end_date)`: Queries each day in the date range to compute total weekly sleep hours, weekly average resting heart rate (HRrest), and overnight HRV average.

5. **[`server.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/server.py)**
   - FastAPI backend providing REST endpoints:
     - `GET /api/dashboard`: Aggregated weekly volumes, Strava workouts, and Garmin biometrics.
     - `POST /api/ai/coach`: Generates LLM coaching feedback and readiness evaluation.
     - `GET /api/predictions`: Computes race predictions (5K, 10K, 21.1K, 42.2K).
     - `POST /api/sheet/sync`: Triggers Google Sheets synchronization from the web interface.
   - Serves the Single Page Application from `web/`.

6. **[`ai_coach.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/ai_coach.py)**
   - AI Coaching & Race Prediction engine.
   - Integrates with Gemini LLM (`gemini-2.5-flash`) when `GEMINI_API_KEY` is present, with robust heuristic coaching fallbacks.
   - Implements Peter Riegel formula for race time & pacing predictions.

7. **[`web/`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/web/)**
   - Single Page Web App with a responsive dark-mode glassmorphism design:
     - `index.html`: Dashboard structure, metrics cards, 7-day coaching calendar, AI panel, feedback editor.
     - `styles.css`: Custom CSS design system with Outfit/Inter typography, neon gradients, and micro-animations.
     - `app.js`: Chart.js charts, dynamic week selection, AI coaching generation, and Google Sheets sync triggers.


---

## 📊 Google Sheets Layout Formats

### 1. Old Layout (Rows 13 – 66)
*   **Structure:** 1 row per week.
*   **Column A:** Week date range (e.g. `17-23/8/'26`), sport totals, total training hours, and sleep/HRV tracker.
*   **Columns B–H (Mon–Sun):** Contains coach program and appended daily Strava data.

### 2. New Layout (Row 67 onwards)
*   **Structure:** 7-row block per week.
    *   **Row 1 ($R$):** Week date range (e.g. `24/8–30/8/2026`) in Column A, Phase in Column E.
    *   **Row 2 ($R+1$):** Empty spacer.
    *   **Row 3 ($R+2$):** Day headers (`ΔΕΥ 24/8`, etc.).
    *   **Row 4 ($R+3$):** `ΠΡΟΓΡΑΜΜΑ` (Coach program).
    *   **Row 5 ($R+4$):** `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ` (Athlete feedback & daily Strava workout logs).
    *   **Row 6 ($R+5$):** `ΕΒΔΟΜΑΔΑ` (Weekly summary & totals: Running, Cycling, Swimming, Sleep, HRrest, HRV).
    *   **Row 7 ($R+6$):** Empty spacer.

---

## ⚙️ Environment Variables (`.env`)

```env
STRAVA_CLIENT_ID=your_strava_client_id
STRAVA_CLIENT_SECRET=your_strava_client_secret
GOOGLE_SHEET_ID=your_google_sheet_id
GARMIN_EMAIL=your_garmin_email@example.com
GARMIN_PASSWORD=your_garmin_password
GEMINI_API_KEY=your_gemini_api_key
```

---

## 🛠️ Development & Execution Commands

This project uses [`uv`](https://astral.sh/uv) for fast Python package and environment management.

```bash
# Install dependencies
uv sync

# Fetch & display Strava activities in terminal only
uv run python main.py --count 30

# Fetch & sync activities to Google Sheets (including Garmin health metrics)
uv run python main.py --sheet --count 30

# Test code syntax & compile
uv run python -m py_compile main.py google_sheets.py garmin_client.py strava_auth.py
```

---

## 🔒 Security & Best Practices

- **Never commit credentials or tokens**: Ensure `.env`, `credentials.json`, `token.json`, `gsheets_token.json`, and `.garmin_tokens/` remain in [`.gitignore`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/.gitignore).
- **Idempotent Sync**: All sync operations can be run repeatedly without duplicating Strava entries or overwriting manual feedback notes.
