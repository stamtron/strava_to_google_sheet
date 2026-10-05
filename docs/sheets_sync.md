# 📊 Google Sheets Sync & Layout Detection

StaminAI synchronizes workout telemetry and biometrics directly into the athlete's coaching spreadsheet in Google Sheets. It dynamically detects sheet formatting patterns to maintain full backward-compatibility with historic training logs.

---

## 📐 Layout Detection Engine

The sync engine automatically inspects row $R+4$ in column A for the marker `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ` to determine the layout:

### 1. Old Single-Row Layout (Rows 13–66)
- **Structure**: 1 spreadsheet row per calendar week.
- **Weekly Totals**: Written into Column A (totals for Run, Bike, Swim, Strength, Total Hours, Garmin tracker).
- **Daily Workouts**: Appended directly into Columns B through H (Monday through Sunday).

### 2. New 7-Row Block Layout (Row 67+)
- **Structure**: Each calendar week occupies a dedicated 7-row block:
  - **Row $R$**: Days of the week header (`ΔΕΥΤΕΡΑ` to `ΚΥΡΙΑΚΗ`).
  - **Row $R+1$**: Coach's prescribed morning workouts.
  - **Row $R+2$**: Coach's prescribed afternoon workouts.
  - **Row $R+3$**: Reserved note rows.
  - **Row $R+4$ (`ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ`)**: Completed Strava workouts appended under `── Strava Data ──`.
  - **Row $R+5$ (`ΕΒΔΟΜΑΔΑ`)**: Weekly summary totals (Running, Cycling, Swimming, Strength, Total Training Hours, and Garmin Biometrics tracker).
  - **Row $R+6$**: Spacing separator.

---

## 🏃 Sport Data Formatting Rules

StaminAI standardizes telemetry formatting specifically for endurance coaches:

- **Running**:
  - Distance in kilometers (e.g. `12.4 km`).
  - Pace formatted in minutes and seconds per kilometer (e.g. `4:35 /χλμ`).
- **Cycling**:
  - Distance in kilometers.
  - Speed formatted in kilometers per hour (e.g. `31.2 χλμ/ω`).
  - Indoor rides reporting 0 km distance are automatically estimated based on moving time and `INDOOR_BIKE_SPEED_KMH` (default: 21.0 km/h).
- **Swimming**:
  - Distance corrected via `SWIM_DISTANCE_DIVISOR` (default: `2.0`) to compensate for sports-watch lap double-counting.
  - Pace formatted in time per 100 meters (e.g. `1:28 /100μ`).
- **Strength Training (Ενδυνάμωση)**:
  - Logged with duration and exercise details.
- **Garmin Health Tracker**:
  - Formatted into the weekly summary:
    `Ύπνος __h • HRrest __ • HRV __`

---

## 🛡️ API Quota & Exponential Backoff

The Google Sheets API enforces strict rate limits (60 read requests and 60 write requests per minute per user).

StaminAI wraps every Google API call in an exponential backoff retry handler (`execute_with_retry`):
- Retries on transient HTTP 429 and 503 errors up to `GSHEETS_MAX_RETRIES` (default: 3).
- Uses exponential jitter delays ($1\text{s} \to 2\text{s} \to 4\text{s}$) to gracefully ride out Google quota windows without failing the sync.
