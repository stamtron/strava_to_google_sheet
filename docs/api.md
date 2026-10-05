# 🌐 REST API Reference

StaminAI provides a complete FastAPI REST backend (`server.py`) serving dashboard data, telemetry analytics, AI coaching, webhooks, and device sync.

Base URL: `http://127.0.0.1:8000`

---

## 📊 Dashboard & Telemetry

### `GET /api/dashboard`
Fetches dashboard state including current week metrics, multi-week progression history, ACWR trends, weather forecast, and Karvonen HR zone balance.
- **Parameters**: `weeks` (optional int, default 8).
- **Response**: `{ status, current_week, progression, acwr, durability, recovery, weather, hr_zones }`.

### `GET /api/activities`
Queries Strava activities from local storage or cached API fetches.
- **Parameters**:
  - `limit` (int, default 30)
  - `sport_type` (optional string, e.g. `Run`, `Ride`, `Swim`)
  - `before` / `after` (optional ISO date strings)
- **Response**: `[ { id, name, sport_type, distance, moving_time, ... } ]`.

### `GET /api/weather`
Returns Athens, Greece (or configured coordinates) daily historical and 7-day forecast weather.
- **Response**: `{ city, coordinates, forecast: [ { date, temp_max, temp_min, rain_mm, rain_prob, wind_kmh, wmo_code } ] }`.

---

## 🏃 Durability, Compliance & Gear

### `GET /api/durability`
Returns the run durability assessment, Foster monotony & strain, ramp rate, and cross-training prescriptions.
- **Response**: `{ risk_level, ramp_rate_pct, spacing, monotony, strain, signals, cross_training }`.

### `GET /api/compliance`
Matches Google Sheets planned workouts against completed Strava telemetry for the current week.
- **Response**: `{ overall_score, days: [ { date, planned, actual, delta_km, pace_adherence, score } ] }`.

### `GET /api/gear`
Returns aggregated mileage for running shoes and bicycles, including alert states.
- **Response**: `{ shoes: [ { id, name, distance_km, alert, threshold_km } ], bikes: [ ... ] }`.

---

## ⌚ Garmin Structured Workouts

### `GET /api/workouts/preview`
Parses planned Greek training prescriptions from Google Sheets into structured Garmin workout steps without uploading.
- **Response**: `{ workouts: [ { date, sport, title, steps: [ ... ] } ] }`.

### `POST /api/workouts/sync`
Generates and uploads structured workouts to Garmin Connect, scheduling them onto the user's Garmin Calendar for over-the-air sync.
- **Response**: `{ uploaded_count, scheduled_dates: [ ... ] }`.

---

## 🤖 AI Coach & Conversational Memory

### `POST /api/ai/coach`
Runs a qualitative evaluation of the week's training load and readiness.
- **Payload**: `{ week_index: 0 }`.
- **Response**: `{ advice, readiness_score, focus_areas }`.

### `POST /api/ai/chat`
Sends a conversational turn to the StaminAI Coach agent with automatic tool calling.
- **Payload**: `{ message: string, session_id?: string, week_context?: number }`.
- **Response**: `{ reply: string, session_id: string, tools_used: [ string ] }`.

### `GET /api/coach/memory`
Lists durable facts currently stored in the athlete's memory profile.
- **Response**: `{ facts: [ { id: string, fact: string, category: string, created_at: string } ] }`.

### `DELETE /api/coach/memory/{id}`
Deletes an obsolete or inaccurate fact from memory.
- **Response**: `{ success: true, deleted_id: string }`.

### `POST /api/coach/memory/extract`
Triggers an end-of-conversation extraction pass to persist new durable facts.
- **Payload**: `{ session_id: string }`.
- **Response**: `{ extracted_count: number, new_facts: [ string ] }`.

---

## 🔄 Sync, History & Webhooks

### `GET /api/history/status`
Returns local SQLite activity store status (total activities, date range, last sync timestamp).
- **Response**: `{ count: number, earliest_date: string, latest_date: string, last_sync: string }`.

### `POST /api/history/backfill`
Triggers a paginated historical backfill of the athlete's entire Strava history into local SQLite.
- **Payload**: `{ resume?: boolean }`.
- **Response**: `{ status: "started", message: string }`.

### `POST /api/sheet/sync`
Performs an on-demand sync of Strava activities and Garmin biometrics to Google Sheets.
- **Response**: `{ status: "success", activities_synced: number, weeks_updated: [ string ] }`.

### `GET /api/strava/webhook`
Handles Strava webhook verification challenge handshake.

### `POST /api/strava/webhook`
Ingests real-time activity creation and update events from Strava.

---

## 📱 Telegram Notifications

### `POST /api/notifications/telegram/next-day`
Dispatches tomorrow's workout brief, Athens weather, recovery status, and coach advice to Telegram.

### `POST /api/notifications/telegram/today`
Dispatches today's training brief to Telegram.

### `POST /api/notifications/telegram/webhook`
Receives two-way bot command interactions from Telegram Bot API (`/today`, `/tomorrow`, `/recovery`, `/stats`, `/coach`, etc.).

---

## 🩺 System Health

### `GET /api/health`
Checks server status, SQLite database reachability, and cache availability.
- **Response**: `{ status: "ok", timestamp: string, version: string, database: "connected" }`.
