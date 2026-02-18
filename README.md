# 🏃 Strava to Google Sheet

Fetch your training logs from **Strava** and (soon) sync them to a **Google Sheet** — organized by date.

## Features

- ✅ **OAuth2 Authentication** — Browser-based login with automatic token caching & refresh
- ✅ **Detailed Activity Logs** — Name, type, date, distance, duration, pace, elevation
- ✅ **Heart Rate Data** — Average HR, Max HR, Suffer Score
- ✅ **Extra Metrics** — Calories, temperature, activity description
- 🔜 **Google Sheets Integration** — Auto-sync activities to a spreadsheet by date

## Setup

### 1. Create a Strava API App

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create a new application
3. Set **Authorization Callback Domain** to `localhost`
4. Note your **Client ID** and **Client Secret**

### 2. Configure Environment

```bash
cp .env.example .env  # or just edit .env directly
```

Add your credentials to `.env`:

```
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
```

### 3. Install & Run

```bash
# Install dependencies
uv sync

# Run the script
uv run python main.py
```

On first run, your browser will open for Strava authorization. After that, tokens are cached in `token.json` and refreshed automatically.

## Sample Output

```
  📌 Morning Run
     Mon 2026-02-17 09:53  •  Run
     ────────────────────────────────────────────────────────────
     📏 10.01 km │ ⏱️  54m 41s │ 🏎️  5:27 /km │ ⛰️  11m gain
     ❤️  Avg HR: 155 bpm │ Max HR: 172 bpm │ Suffer Score: 62.0
     🔥 845 cal
```

## Project Structure

```
├── main.py           # Main script — fetches & prints activities
├── strava_auth.py    # OAuth2 authentication module
├── .env              # Your Strava API credentials (git-ignored)
├── token.json        # Cached OAuth tokens (git-ignored, auto-generated)
├── pyproject.toml    # Python project config & dependencies
└── README.md
```

## API Limitations

- **Weather** — Not available via Strava's public API (app-only feature). Device temperature (`average_temp`) is included when recorded.
- **AI Analysis** — Strava's "Athlete Intelligence" is not exposed via the API.

## Tech Stack

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- [requests](https://docs.python-requests.org/) for HTTP
- [python-dotenv](https://github.com/theskumar/python-dotenv) for config
