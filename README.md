# 🏃 Strava & Garmin to Google Sheet

Automatically fetch your workout logs from **Strava** and 24/7 health biometrics from **Garmin Connect** (Sleep, Resting HR, HRV), syncing them into your **Google Sheet** coaching log — organized by date, formatted in Greek, with all key metrics.

## Features

- ✅ **Strava Workouts**: OAuth2 authentication with automatic token refresh.
- ✅ **Garmin 24/7 Health Metrics**: Automatically fetches total weekly Sleep hours, weekly average Resting Heart Rate (HRrest), and overnight HRV from Garmin Connect.
- ✅ **Google Sheets Sync**: Dynamically supports both **Old Single-Row Layout** (rows 13–66) and **New 7-Row Block Layout** (row 67+).
- ✅ **Appends Below Coach Notes**: Preserves coach training instructions and appends Strava data under `── Strava Data ──`.
- ✅ **Weekly Totals**: Automatically sums and writes weekly totals for Running, Cycling, Swimming, Strength Training (Ενδυνάμωση), Total Training Hours, and Garmin Health Tracker (`Ύπνος __h • HRrest __ • HRV __`).
- ✅ **Swimming Pace in /100m**: Formats swimming pace in time per 100 meters (e.g. `1:24 /100μ`).
- ✅ **Swimming Distance Correction**: Halves all swimming distances and average speeds (divided by 2) to correct watch double-counting.
- ✅ **Indoor Cycling Estimation**: Automatically estimates distance for indoor trainer rides based on moving time at a 21 km/h average speed.
- ✅ **Idempotent**: Re-running replaces only the Strava/Garmin data sections and updates weekly totals without duplication.
- ✅ **Optional Automation**: Easy weekly automation via cron.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/stamtron/strava_to_google_sheet.git
cd strava_to_google_sheet

# 2. Install Python 3.11+ and uv (if not already)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Install dependencies
uv sync

# 4. Configure credentials (see Setup below)
cp .env.example .env

# 5. Run
uv run python main.py              # Print Strava activities only
uv run python main.py --sheet      # Sync Strava + Garmin to Google Sheet
uv run python main.py --sheet --count 50   # Fetch last 50 activities
```

---

## Setup

### 1. Strava API

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an application:
   - **Application Name**: `strava_to_google_sheet`
   - **Callback Domain**: `localhost`
3. Copy your **Client ID** and **Client Secret** into `.env`.

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com) — log in with a personal Gmail.
2. Create a new project (e.g., `strava-sheets`).
3. Enable the **Google Sheets API** under **APIs & Services → Library**.
4. Configure the **OAuth consent screen** (External, add your email under Test Users).
5. Create **OAuth Client ID** credentials (type: **Desktop app**), download the JSON, rename it to `credentials.json`, and place it in the project root.

### 3. Garmin Connect (Sleep, HRrest & HRV)

1. Add your Garmin Connect email and password to `.env`:
   ```env
   GARMIN_EMAIL=your_garmin_email@example.com
   GARMIN_PASSWORD=your_password
   ```
2. Session tokens are securely cached in `.garmin_tokens/` so you do not need to re-authenticate or re-enter your password on subsequent runs.
3. If your account has 2-Factor Authentication (2FA) enabled, you will be prompted once in the terminal for the verification code.

### 4. Gemini API Key (Optional - for AI Coach & LLM Insights)

1. Get a free API key from [aistudio.google.com](https://aistudio.google.com/) (Free tier includes 1,500 requests/day for Gemini 2.5 Flash).
2. Add your key to `.env`:
   ```env
   GEMINI_API_KEY=your_gemini_api_key
   ```

### 5. Environment Variables (`.env`)

Create a `.env` file in the project root:

```env
STRAVA_CLIENT_ID=your_strava_client_id
STRAVA_CLIENT_SECRET=your_strava_client_secret
GOOGLE_SHEET_ID=your_sheet_id
GARMIN_EMAIL=your_garmin_email@example.com
GARMIN_PASSWORD=your_garmin_password
GEMINI_API_KEY=your_gemini_api_key
```

> **Tip**: Your Google Sheet ID is the string in the URL: `docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`

---

## How It Works

### Sheet Layout Detection

The sync engine automatically detects the layout of each week in the Google Sheet:
- **Old Single-Row Layout (Rows 13–66):** 1 row per week. Daily workouts are appended to Columns B–H of the row, and sport totals + Garmin health data are updated in Column A.
- **New 7-Row Block Layout (Row 67 onwards):** 7 rows per week. Daily workouts are written into the `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ` row (Row 5 of the block), and weekly totals + Garmin health metrics are updated in Column B of the `ΕΒΔΟΜΑΔΑ` row (Row 6 of the block).

### Garmin Biometric Calculations

For each week, the sync queries Garmin Connect from Monday to Sunday:
- **Total Sleep:** Sums all verified sleep durations across the week (e.g., `51.6h`).
- **Resting Heart Rate (HRrest):** Computes the 7-day average resting heart rate in bpm (e.g., `49`).
- **Overnight HRV:** Computes the weekly average overnight HRV in ms (e.g., `75`).

These are written directly to the sheet:
```text
Ύπνος 51.6h • HRrest 49 • HRV 75
```

---

## Automatic Weekly Sync (cron)

You can schedule the script to run automatically every week using cron.

### Setup

1. **Open your crontab**:
   ```bash
   crontab -e
   ```

2. **Add this line** (runs every Sunday at 10 PM):
   ```cron
   0 22 * * 0 cd /Users/YOUR_USERNAME/Documents/strava_to_google_sheet && /Users/YOUR_USERNAME/.local/bin/uv run python main.py --sheet --count 30 >> /tmp/strava_sync.log 2>&1
   ```

---

## Project Structure

```
├── main.py              # CLI entry point & terminal display
├── google_sheets.py     # Google Sheets integration, dynamic layout & formatting
├── garmin_client.py     # Garmin Connect API client & biometric calculations
├── strava_auth.py       # Strava OAuth2 authentication & token refresh
├── AGENTS.md            # Technical architecture guide for AI agents & developers
├── credentials.json     # Google OAuth credentials (git-ignored)
├── .env                 # API credentials (git-ignored)
├── token.json           # Strava token cache (git-ignored, auto-generated)
├── gsheets_token.json   # Google token cache (git-ignored, auto-generated)
├── .garmin_tokens/      # Garmin session cache (git-ignored, auto-generated)
├── pyproject.toml       # Dependencies
└── README.md
```

## Tech Stack

- Python 3.11+ / [uv](https://docs.astral.sh/uv/)
- [requests](https://docs.python-requests.org/) — Strava API
- [garminconnect](https://github.com/cyberjunky/python-garminconnect) — Garmin Connect API
- [google-api-python-client](https://github.com/googleapis/google-api-python-client) — Google Sheets API
- [python-dotenv](https://github.com/theskumar/python-dotenv) — Environment config

