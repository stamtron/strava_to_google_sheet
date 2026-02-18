# 🏃 Strava to Google Sheet

Fetch your training logs from **Strava** and sync them to your **Google Sheet** coaching log — organized by date, formatted in Greek.

## Features

- ✅ **Strava OAuth2 Authentication** — Browser-based login with automatic token caching & refresh
- ✅ **Detailed Activity Logs** — Name, type, date, distance, duration, pace, elevation
- ✅ **Heart Rate Data** — Average HR, Max HR, Suffer Score
- ✅ **Extra Metrics** — Calories, temperature, description
- ✅ **Google Sheets Sync** — Auto-writes activities to the correct cell by date
- ✅ **Greek Formatting** — Matches the existing coaching sheet style

## Setup

### 1. Strava API App

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an application, set **Callback Domain** to `localhost`
3. Copy your **Client ID** and **Client Secret**

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Enable the **Google Sheets API**
3. Create **OAuth 2.0 Client ID** (Desktop app) → download as `credentials.json`
4. Place `credentials.json` in the project root
5. Share your Google Sheet with your Google account (the one you'll authorize with)

### 3. Configure Environment

```bash
# Edit .env with your credentials
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
GOOGLE_SHEET_ID=your_sheet_id    # from the URL: docs.google.com/spreadsheets/d/{THIS_PART}/edit
```

### 4. Install & Run

```bash
# Install dependencies
uv sync

# Print activities only
uv run python main.py

# Print AND sync to Google Sheet
uv run python main.py --sheet

# Fetch more/fewer activities
uv run python main.py --sheet --count 50
```

## How It Works

### Sheet Mapping

The script maps each activity to the correct cell in your coaching sheet:

| Sheet Column | Day |
|---|---|
| B | Δευτέρα (Monday) |
| C | Τρίτη (Tuesday) |
| D | Τετάρτη (Wednesday) |
| E | Πέμπτη (Thursday) |
| F | Παρασκευή (Friday) |
| G | Σάββατο (Saturday) |
| H | Κυριακή (Sunday) |

Column A contains week ranges (e.g., `1-7/9/'25`). The script calculates the correct row based on the activity date.

### Cell Format (Greek)

Multiple activities on the same day are combined with `---` separators:

```
Τρέξιμο: Morning Run
Απόσταση: 10.01 χλμ
Συνολικός χρόνος: 54λ 41δ
Μέσος ρυθμός: 5:27 /χλμ
Μέσοι καρδιακοί παλμοί: 155
Μέγιστοι καρδιακοί παλμοί: 172
Θερμίδες: 845
---
Βάρη: Evening Weight Training
Συνολικός χρόνος: 47λ 35δ
Μέσοι καρδιακοί παλμοί: 116
Θερμίδες: 252
```

## Project Structure

```
├── main.py           # CLI — fetches activities, prints, optionally syncs
├── strava_auth.py    # Strava OAuth2 authentication
├── google_sheets.py  # Google Sheets integration & formatting
├── credentials.json  # Google OAuth credentials (git-ignored)
├── .env              # API credentials (git-ignored)
├── token.json        # Strava OAuth tokens (git-ignored, auto-generated)
├── gsheets_token.json # Google OAuth tokens (git-ignored, auto-generated)
├── pyproject.toml    # Dependencies
└── README.md
```

## API Limitations

- **Weather** — Not available via Strava API. Device temperature is included when recorded.
- **AI Analysis** — Strava's "Athlete Intelligence" is not exposed via the API.

## Tech Stack

- Python 3.11+ / [uv](https://docs.astral.sh/uv/)
- [requests](https://docs.python-requests.org/) — Strava API
- [google-api-python-client](https://github.com/googleapis/google-api-python-client) — Google Sheets API
- [python-dotenv](https://github.com/theskumar/python-dotenv) — Config
