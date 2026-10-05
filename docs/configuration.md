# ⚙️ Configuration Reference

All settings in StaminAI are centralized in [`src/config.py`](../src/config.py) and loaded from `.env`. Only credentials are strictly required; everything else provides sensible defaults tailored for endurance multi-sport training.

---

## 🔑 Core Credentials & Service Authentication

| Variable | Default | Description |
| --- | --- | --- |
| `STRAVA_CLIENT_ID` | — | Strava OAuth2 Application Client ID |
| `STRAVA_CLIENT_SECRET` | — | Strava OAuth2 Application Client Secret |
| `GOOGLE_SHEET_ID` | — | Google Sheet ID (from the spreadsheet URL); required for `--sheet` |
| `GARMIN_EMAIL` | — | Garmin Connect login email address |
| `GARMIN_PASSWORD` | — | Garmin Connect login password |
| `GEMINI_API_KEY` | — | Google Gemini API key for AI Coach and workout parsing |
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot token from @BotFather for push briefings |
| `TELEGRAM_CHAT_ID` | — | Telegram recipient user ID for training briefs |
| `TELEGRAM_WEBHOOK_SECRET` | — | Secret token verified against `X-Telegram-Bot-Api-Secret-Token` |

---

## 🌐 Server & Network Settings

| Variable | Default | Description |
| --- | --- | --- |
| `SERVER_HOST` | `127.0.0.1` | Dashboard bind address (loopback only by default for security) |
| `SERVER_PORT` | `8000` | Web server port |
| `ALLOWED_ORIGINS` | `http://localhost:8000,...` | Comma-separated CORS origins |
| `STRAVA_REDIRECT_PORT` | `8123` | Local OAuth callback port; **must differ from `SERVER_PORT`** |
| `STRAVA_DETAIL_DELAY_SEC` | `0.15` | Throttling delay (seconds) between per-activity detail requests |
| `STRAVA_MAX_RETRIES` | `3` | Maximum retries on HTTP 429 rate limit responses |
| `GSHEETS_MAX_RETRIES` | `3` | Maximum retries with exponential backoff on Google API quota errors |

---

## 💾 Caching & Database Settings

| Variable | Default | Description |
| --- | --- | --- |
| `ACTIVITIES_CACHE_TTL` | `600` | Strava activity memory/JSON cache lifetime (seconds) |
| `GARMIN_CACHE_TTL` | `21600` | In-progress Garmin week cache lifetime (6 hours; finished weeks are cached permanently) |
| `WEATHER_CACHE_TTL` | `10800` | Open-Meteo weather forecast cache lifetime (3 hours) |
| `STRAVA_BACKFILL_PAGE_SIZE` | `200` | Activities per backfill page (Strava's API cap) |
| `STRAVA_BACKFILL_MAX_PAGES` | `100` | Maximum pages runaway guard during backfill |
| `STRAVA_BACKFILL_PAGE_DELAY_SEC`| `0.5` | Pause between backfill pages to protect rate limits |

---

## 📍 Location & Weather Settings

| Variable | Default | Description |
| --- | --- | --- |
| `ATHLETE_CITY` | `Athens, Greece` | Athlete home city for Open-Meteo weather forecasts |
| `ATHLETE_LATITUDE` | `37.9838` | Latitude coordinate for daily weather |
| `ATHLETE_LONGITUDE` | `23.7275` | Longitude coordinate for daily weather |
| `ATHLETE_TIMEZONE` | `Europe/Athens` | Timezone for daily weather rollups and telegram schedules |
| `BRIEF_RAIN_THRESHOLD_MM` | `1.0` | Minimum rain (mm) to trigger weather brief alerts |
| `BRIEF_HEAT_THRESHOLD_C` | `22.0` | Temperature (°C) to trigger heat & hydration warnings |

---

## 🏃 Physiology & Sports-Science Tunables

| Variable | Default | Description |
| --- | --- | --- |
| `HR_MAX` | `185` | Maximum heart rate for Karvonen zones and TRIMP stress |
| `HR_REST` | `50` | Resting heart rate for Karvonen Heart Rate Reserve |
| `SWIM_DISTANCE_DIVISOR` | `2.0` | Divisor to correct watch double-counting in pool swims |
| `INDOOR_BIKE_SPEED_KMH` | `21.0` | Estimated speed (km/h) for indoor trainer rides reporting 0 km |
| `ACWR_CHRONIC_WEEKS` | `4` | Weeks in the chronic baseline window |
| `ACWR_MIN_CHRONIC_WEEKS` | `2` | Minimum chronic history required before ACWR ratio is reported |
| `RUN_RAMP_SAFE_PCT` | `10.0` | Safe weekly run volume ramp percentage (the 10% rule) |
| `RUN_LONG_RUN_MAX_SHARE` | `0.40` | Maximum safe share of weekly run volume in a single long run |
| `RUN_MIN_REST_DAYS` | `2` | Minimum recommended non-running days per week |
| `MONOTONY_WARN_THRESHOLD` | `2.0` | Foster training monotony warning threshold |
| `STRAIN_WARN_THRESHOLD` | `1500.0` | Foster training strain warning threshold |
| `AQUA_JOG_LOAD_FACTOR` | `0.90` | Run-equivalent stimulus per minute of aqua jogging |
| `BIKE_RUN_LOAD_FACTOR` | `0.55` | Run-equivalent stimulus per minute of cycling |
| `CROSS_TRAINING_AQUA_SHARE` | `0.40` | Aqua-jogging share in suggested cross-training volume |
| `CROSS_TRAINING_BIKE_SHARE` | `0.60` | Cycling share in suggested cross-training volume |
| `SHOE_ALERT_KM` | `650` | Mileage threshold to warn for running shoe replacement |

---

## 🤖 AI Coach & Persistent Memory

| Variable | Default | Description |
| --- | --- | --- |
| `GEMINI_MODELS` | `gemini-3.6-flash,gemini-3.7-flash,gemini-3.8-flash,gemini-flash-latest` | Fallback chain for the one-shot weekly coaching panel |
| `COACH_CHAT_MODELS` | `gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-flash-latest` | Fallback chain for conversational agent (must support function calling) |
| `COACH_MAX_TOOL_CALLS` | `8` | Ceiling on tool invocations per conversational turn |
| `COACH_SESSION_TTL` | `604800` | Idle conversation lifetime in seconds (7 days) |
| `COACH_MAX_HISTORY_MESSAGES` | `24` | Maximum conversation turns replayed in prompt context |
| `COACH_MEMORY_BACKEND` | `sqlite` | Long-term memory backend: `sqlite` (keyword) or `chroma` (semantic vector) |
| `COACH_MEMORY_TOP_K` | `5` | Maximum facts recalled and injected per conversational turn |
| `COACH_EMBEDDING_MODEL` | `gemini-embedding-001` | Embedding model used when `COACH_MEMORY_BACKEND=chroma` |
| `COACH_MEMORY_COLLECTION` | `athlete_memory` | ChromaDB collection name |
| `COACH_AUTO_FACT_LIMIT` | `5` | Maximum facts extracted in an end-of-session background pass |

---

## ⏱️ Athlete Race PBs & Baselines

These values calibrate the **Peter Riegel race predictor** and define the athlete baseline profile for the conversational coach.

> [!IMPORTANT]
> The `ATHLETE_PB_*` values should be **official race results, not training bests**. Default values represent the repository author's official race times. Update these in your `.env` with your personal verified race times:

| Variable | Default | Description |
| --- | --- | --- |
| `ATHLETE_PB_HALF_MARATHON_SEC` | `6415` | Official Half Marathon PB (1h 46m 55s) |
| `ATHLETE_PB_10K_SEC` | `3015` | Official 10K PB (50m 15s) |
| `ATHLETE_PB_5K_SEC` | `1395` | Official 5K PB (23m 15s) — run baseline in PB mode |
| `ATHLETE_PB_SPRINT_TRI_SEC` | `4532` | Official Sprint Triathlon PB (1h 15m 32s) |
| `ATHLETE_PB_OLYMPIC_TRI_SEC` | `8647` | Official Olympic Triathlon PB (2h 24m 07s) |
| `ATHLETE_PB_AQUATHLON_SEC` | `2234` | Official Aquathlon PB (37m 14s) |
| `ATHLETE_RACE_SWIM_100M_SEC` | `100.0` | Race-day swim pace baseline (seconds per 100m, 1:40/100m) |
| `ATHLETE_RACE_BIKE_SPEED_KMH` | `32.5` | Race-day cycling speed baseline (km/h) |
