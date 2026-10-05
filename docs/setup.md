# 🛠️ Setup & Installation Guide

This guide walks you through setting up StaminAI, configuring all external service credentials, and running the system via CLI or the Web Dashboard.

---

## 📋 Prerequisites

- **Python 3.11+**
- **[uv](https://github.com/astral-sh/uv)** (fast Python package and project manager)
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Or via Homebrew (macOS)
  brew install uv
  ```

---

## 🚀 Quick Installation

```bash
# 1. Clone the repository
git clone https://github.com/stamtron/strava_to_google_sheet.git
cd strava_to_google_sheet

# 2. Install dependencies (creates virtual environment automatically)
uv sync

# 3. Create your local environment configuration
cp .env.example .env
```

---

## 🔑 External Service Configuration

### 1. Strava API (OAuth2)
1. Go to the [Strava API Settings](https://www.strava.com/settings/api).
2. Create an API application if you haven't already.
3. Set the **Authorized Callback Domain** to `localhost`.
   > [!NOTE]
   > Strava validates only the domain `localhost`. The local OAuth listener port (`STRAVA_REDIRECT_PORT`, default `8123`) does not need separate registration in the Strava portal. It is deliberately distinct from `SERVER_PORT` (`8000`) so the web dashboard and OAuth callback handler do not collide.
4. Copy your **Client ID** and **Client Secret** into `.env`:
   ```env
   STRAVA_CLIENT_ID=your_client_id
   STRAVA_CLIENT_SECRET=your_client_secret
   ```
5. On first sync or run, a browser window will open asking you to authorize StaminAI. Cached tokens are saved to `token.json` and refreshed automatically.

### 2. Google Sheets API
1. Navigate to the [Google Cloud Console](https://console.cloud.google.com).
2. Create a new project (e.g., `StaminAI`) and enable the **Google Sheets API**.
3. Go to **APIs & Services > Credentials**, click **Create Credentials > OAuth Client ID**.
4. Choose **Desktop App** as the Application type.
5. Download the credentials file and save it in the project root as `credentials.json`.
6. Open your target Google Sheet in your browser and extract the Sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit`
7. Add the ID to `.env`:
   ```env
   GOOGLE_SHEET_ID=your_spreadsheet_id_here
   ```
8. The first time you run `--sheet`, a Google authentication flow will generate `gsheets_token.json`.

### 3. Garmin Connect (24/7 Biometrics)
1. Provide your Garmin Connect email and password in `.env`:
   ```env
   GARMIN_EMAIL=your_garmin_email@example.com
   GARMIN_PASSWORD=your_password
   ```
2. StaminAI automatically logs in, handles MFA if prompted, and caches session tokens in the `.garmin_tokens/` directory to prevent repeated logins.
3. Health metrics (Sleep duration, Resting Heart Rate, overnight HRV, Body Battery, and Stress) are automatically queried and cached in `.garmin_cache.json`.

### 4. Gemini AI (StaminAI Coach)
1. Get a free API key from [Google AI Studio](https://aistudio.google.com/).
2. Add your key to `.env`:
   ```env
   GEMINI_API_KEY=your_gemini_api_key
   ```
3. If no key is provided, the platform gracefully falls back to deterministic sports-science heuristics.
4. Model fallback chains are configurable in `.env`:
   ```env
   GEMINI_MODELS=gemini-3.6-flash,gemini-3.7-flash,gemini-3.8-flash,gemini-flash-latest
   COACH_CHAT_MODELS=gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-flash-latest
   ```

### 5. Telegram Bot & Daily Briefings (Optional)
1. Create a bot using [@BotFather](https://t.me/BotFather) on Telegram and obtain your bot token.
2. Obtain your personal Telegram numeric chat ID (via [@userinfobot](https://t.me/userinfobot) or similar).
3. Add them to `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
   TELEGRAM_CHAT_ID=12345678
   TELEGRAM_DAILY_DISPATCH_TIME=20:30
   ```
4. If using Telegram Webhooks, specify:
   ```env
   TELEGRAM_WEBHOOK_SECRET=your_random_secret_token
   ```

---

## 🏃 Running the Application

### CLI Commands

```bash
# Print recent Strava activities directly in your terminal
uv run python main.py

# Sync Strava activities and Garmin biometrics to Google Sheets
uv run python main.py --sheet

# Fetch a custom number of recent activities (e.g. 50)
uv run python main.py --sheet --count 50

# One-time full history backfill into local SQLite database
uv run python main.py --backfill

# Restart backfill from page 1 without resuming previous cursor
uv run python main.py --backfill --no-resume

# Dispatch daily briefings to Telegram manually
uv run python main.py --telegram-next-day
uv run python main.py --telegram-today
```

### Web Dashboard & Server

```bash
# Start the FastAPI web server on http://127.0.0.1:8000
uv run python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser to view the interactive glassmorphic dashboard, 7-day calendar, volume progression, durability assessment, and the conversational AI Coach drawer.

### Running Tests

The test suite is 100% offline and requires no network connections:

```bash
uv run pytest
```
