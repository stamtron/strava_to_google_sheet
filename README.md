# 🏃 Strava to Google Sheet

Automatically fetch your training logs from **Strava** and sync them to your **Google Sheet** coaching log — organized by date, formatted in Greek, with all key metrics.

## Features

- ✅ Strava OAuth2 authentication with automatic token refresh
- ✅ Detailed activity data: distance, pace, HR, calories, temperature, RPE
- ✅ Google Sheets sync — writes to the correct cell by date
- ✅ **Appends** below existing coach instructions (never overwrites)
- ✅ Greek formatting matching the coaching sheet style
- ✅ **Weekly Totals (Column A)**: Automatically sums and writes weekly totals for Running, Cycling, Swimming, Strength Training (Ενδυνάμωση), and overall Training Hours (Συνολικές ώρες προπόνησης) by replacing placeholders in Column A.
- ✅ **Swimming Distance Correction**: Halves all swimming distances and average speeds (divided by 2) to correct watch double-counting.
- ✅ **Indoor Cycling Estimation**: Automatically estimates distance for indoor trainer rides (marked as trainer and with `< 0.1 km` distance) based on moving time at a 21 km/h average speed.
- ✅ Idempotent — re-running replaces only the Strava data section and updates the weekly totals correctly
- ✅ Optional weekly automation via cron

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

# 4. Configure (see Setup sections below)
cp .env.example .env    # Edit with your credentials

# 5. Run
uv run python main.py              # Print activities only
uv run python main.py --sheet      # Print + sync to Google Sheet
uv run python main.py --sheet --count 50   # Fetch more activities
```

---

## Setup

### 1. Strava API

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an application:
   - **Application Name**: anything (e.g., `strava_to_google_sheet`)
   - **Category**: choose any
   - **Callback Domain**: `localhost`
3. Note your **Client ID** and **Client Secret**

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com) — log in with a **personal Gmail** (not a Workspace account)
2. Create a new project (e.g., `strava-sheets`)
3. Enable the **Google Sheets API**:
   - Go to **APIs & Services → Library**
   - Search "Google Sheets API" → **Enable**
4. Configure **OAuth consent screen**:
   - Go to **APIs & Services → OAuth consent screen**
   - Choose **External** → fill in app name + your email
   - Under **Test users**, add your Gmail address
   - Click **Save and Continue** through all steps
5. Create **OAuth credentials**:
   - Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Click **Create** → **Download JSON**
   - Rename the file to `credentials.json` and place it in the project root

### 3. Environment Variables

Create a `.env` file in the project root:

```env
STRAVA_CLIENT_ID=your_strava_client_id
STRAVA_CLIENT_SECRET=your_strava_client_secret
GOOGLE_SHEET_ID=your_sheet_id
```

> **Tip**: Your Google Sheet ID is the long string in the URL:
> `docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`

### 4. First Run (Interactive)

The first run opens your browser twice for authorization:

```bash
uv run python main.py --sheet --count 5
```

1. **Strava**: Log in and authorize → token saved to `token.json`
2. **Google**: Choose your Gmail account → click "Continue" past the unverified app warning → token saved to `gsheets_token.json`

Subsequent runs use cached tokens automatically (no browser needed).

---

## How It Works

### Sheet Mapping

Each activity is placed in the correct cell based on its date:

| Column | Day |
|--------|-----|
| B | Δευτέρα (Monday) |
| C | Τρίτη (Tuesday) |
| D | Τετάρτη (Wednesday) |
| E | Πέμπτη (Thursday) |
| F | Παρασκευή (Friday) |
| G | Σάββατο (Saturday) |
| H | Κυριακή (Sunday) |

The row is calculated from the week date range in Column A (starting from `1-7/9/'25`).

### Cell Format

Strava data is appended below existing content with a separator:

```
[Coach's training instructions stay here]

── Strava Data ──
Τρέξιμο: Morning Run
Απόσταση: 10.01 χλμ
Συνολικός χρόνος: 54λ 41δ
Μέσος ρυθμός: 5:27 /χλμ
Μέσοι καρδιακοί παλμοί: 155
Μέγιστοι καρδιακοί παλμοί: 172
Θερμίδες: 845
Αντιληπτή κόπωση προπόνησης - RPE (1-10): 8
```

Multiple activities on the same day are separated with `---`.

---

## Automatic Weekly Sync (cron)

You can schedule the script to run automatically every week using cron.

### Setup

1. **Make sure the first run is done** (tokens must already exist)

2. **Open your crontab**:
   ```bash
   crontab -e
   ```

3. **Add this line** (runs every Sunday at 10 PM):
   ```cron
   0 22 * * 0 cd /Users/YOUR_USERNAME/Documents/strava_to_google_sheet && /Users/YOUR_USERNAME/.local/bin/uv run python main.py --sheet --count 10 >> /tmp/strava_sync.log 2>&1
   ```

   > Replace `YOUR_USERNAME` with your actual username. Adjust the schedule as needed.

### Cron Schedule Examples

| Schedule | Cron Expression |
|----------|----------------|
| Every Sunday at 10 PM | `0 22 * * 0` |
| Every Monday at 8 AM | `0 8 * * 1` |
| Every day at midnight | `0 0 * * *` |
| Every 6 hours | `0 */6 * * *` |

### Verify It Works

```bash
# Check the log after the scheduled time
cat /tmp/strava_sync.log
```

---

## Project Structure

```
├── main.py              # CLI entry point
├── strava_auth.py       # Strava OAuth2 authentication
├── google_sheets.py     # Google Sheets integration & formatting
├── credentials.json     # Google OAuth credentials (git-ignored)
├── .env                 # API credentials (git-ignored)
├── token.json           # Strava token cache (git-ignored, auto-generated)
├── gsheets_token.json   # Google token cache (git-ignored, auto-generated)
├── pyproject.toml       # Dependencies
└── README.md
```

## API Limitations

- **Weather**: Not available via Strava API. Device temperature is included when recorded.
- **AI Analysis**: Strava's "Athlete Intelligence" is not exposed via the API.

## Tech Stack

- Python 3.11+ / [uv](https://docs.astral.sh/uv/)
- [requests](https://docs.python-requests.org/) — Strava API
- [google-api-python-client](https://github.com/googleapis/google-api-python-client) — Google Sheets API
- [python-dotenv](https://github.com/theskumar/python-dotenv) — Environment config
