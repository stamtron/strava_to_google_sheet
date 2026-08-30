"""
Google Sheets Integration Module.

Authenticates with Google Sheets API via OAuth2,
dynamically detects Old vs New weekly layouts,
and writes formatted Strava & Garmin training data.
"""

import os
import re
from datetime import date, datetime, timedelta

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.config import (
    CREDENTIALS_FILE,
    GSHEETS_SCOPES,
    GSHEETS_TOKEN_FILE,
    GOOGLE_SHEET_ID,
    SHEET_NAME,
)
from src.integrations.garmin import get_garmin_client, get_weekly_health_summary


def _get_week_start(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def parse_date_range(text: str) -> tuple[date, date] | None:
    """
    Parse a date range string from Column A.
    Supports formats like:
      - '17-23/8/\'26' (2-digit year)
      - '24/8–30/8/2026' (4-digit year, en-dash)
      - '1-7/9/\'25'
    """
    if not text:
        return None

    cleaned = text.strip().replace("–", "-").replace("—", "-")
    cleaned = cleaned.replace("'", "").replace("’", "").replace("`", "")

    # Pattern 1: Day-Day/Month/Year (e.g. 17-23/8/26 or 17-23/8/2026)
    m = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})", cleaned)
    if m:
        d1, d2, month, yr = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        year = 2000 + yr if yr < 100 else yr
        try:
            start = date(year, month, d1)
            end = date(year, month, d2)
            return start, end
        except ValueError:
            pass

    # Pattern 2: Day/Month-Day/Month/Year (e.g. 24/8-30/8/2026 or 24/8-30/8/26)
    m2 = re.match(r"^(\d{1,2})\s*/\s*(\d{1,2})\s*-\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})", cleaned)
    if m2:
        d1, m1, d2, m2_m, yr = int(m2.group(1)), int(m2.group(2)), int(m2.group(3)), int(m2.group(4)), int(m2.group(5))
        year = 2000 + yr if yr < 100 else yr
        try:
            start = date(year, m1, d1)
            end = date(year, m2_m, d2)
            return start, end
        except ValueError:
            pass

    return None


def get_sheets_service():
    """Authenticate and return Google Sheets API service."""
    creds = None
    if os.path.exists(GSHEETS_TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(GSHEETS_TOKEN_FILE, GSHEETS_SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Credentials file not found at '{CREDENTIALS_FILE}'.\n"
                    "Download it from Google Cloud Console as 'credentials.json'."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, GSHEETS_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(GSHEETS_TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("sheets", "v4", credentials=creds)


def format_duration_short(seconds: int) -> str:
    """Format seconds into Greek duration e.g. 1ω 24λ."""
    if seconds <= 0:
        return "0λ"
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    if hrs > 0:
        return f"{hrs}ω {mins}λ"
    return f"{mins}λ"


def format_duration(seconds: int) -> str:
    """Format seconds into Greek duration with seconds e.g. 1ω 24λ 30δ."""
    if seconds <= 0:
        return "0δ"
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hrs > 0:
        parts.append(f"{hrs}ω")
    if mins > 0:
        parts.append(f"{mins}λ")
    if secs > 0 or not parts:
        parts.append(f"{secs}δ")
    return " ".join(parts)


def format_pace(avg_speed: float, sport_type: str = "") -> str:
    """Format speed into pace e.g. 5:27 /χλμ or 1:24 /100μ for swim."""
    if avg_speed <= 0:
        return "N/A"
    if sport_type == "Swim":
        # avg_speed is in m/s (halved). Pace per 100m = 100 / avg_speed
        pace_seconds = 100.0 / avg_speed
        mins = int(pace_seconds // 60)
        secs = int(pace_seconds % 60)
        return f"{mins}:{secs:02d} /100μ"
    elif sport_type in ["Ride", "VirtualRide", "GravelRide", "MountainBikeRide"]:
        speed_kmh = avg_speed * 3.6
        return f"{speed_kmh:.1f} χλμ/ω"
    else:
        # Running / walking: min/km
        pace_seconds = 1000.0 / avg_speed
        mins = int(pace_seconds // 60)
        secs = int(pace_seconds % 60)
        return f"{mins}:{secs:02d} /χλμ"


def format_activity_for_cell(act: dict, detail: dict | None = None) -> str:
    """Format a single activity for the cell (Greek)."""
    sport = act.get("sport_type") or act.get("type", "Unknown")
    name = act.get("name", "")
    moving_time = act.get("moving_time", 0)
    raw_distance = act.get("distance", 0)
    avg_speed = act.get("average_speed", 0)
    is_trainer = act.get("trainer", False)

    # Sport name translation
    sport_names = {
        "Run": "Τρέξιμο",
        "TrailRun": "Ορεινό Τρέξιμο",
        "Ride": "Ποδηλασία",
        "VirtualRide": "Virtual Ποδηλασία",
        "GravelRide": "Gravel Ποδηλασία",
        "MountainBikeRide": "Mountain Bike",
        "Swim": "Κολύμβηση",
        "WeightTraining": "Ενδυνάμωση",
        "Workout": "Προπόνηση",
        "Walk": "Περπάτημα",
        "Hike": "Πεζοπορία",
    }
    sport_el = sport_names.get(sport, sport)

    # Swimming correction (halve distance and speed)
    if sport == "Swim":
        raw_distance = raw_distance / 2.0
        avg_speed = avg_speed / 2.0

    # Indoor cycling estimation
    if is_trainer and raw_distance < 100 and moving_time > 0:
        raw_distance = (moving_time / 3600.0) * 21000.0
        avg_speed = 21.0 / 3.6

    distance_km = raw_distance / 1000.0

    lines = [f"{sport_el}: {name}"]
    if distance_km > 0:
        lines.append(f"Απόσταση: {distance_km:.2f} χλμ")
    lines.append(f"Συνολικός χρόνος: {format_duration(moving_time)}")

    if avg_speed > 0:
        pace_str = format_pace(avg_speed, sport)
        if sport == "Swim":
            lines.append(f"Μέσος ρυθμός: {pace_str}")
        elif sport in ["Ride", "VirtualRide", "GravelRide", "MountainBikeRide"]:
            lines.append(f"Μέση ταχύτητα: {pace_str}")
        else:
            lines.append(f"Μέσος ρυθμός: {pace_str}")

    elev = act.get("total_elevation_gain", 0)
    if elev > 0:
        lines.append(f"Υψομετρικά: {elev:.0f} μ")

    avg_hr = act.get("average_heartrate")
    max_hr = act.get("max_heartrate")
    if avg_hr:
        lines.append(f"Μέσοι καρδιακοί παλμοί: {avg_hr:.0f}")
    if max_hr:
        lines.append(f"Μέγιστοι καρδιακοί παλμοί: {max_hr:.0f}")

    if detail:
        calories = detail.get("calories", 0)
        if calories > 0:
            lines.append(f"Θερμίδες: {calories:.0f}")
        temp = detail.get("average_temp")
        if temp is not None:
            lines.append(f"Θερμοκρασία: {temp:.0f}°C")
        suffer = detail.get("suffer_score")
        if suffer:
            lines.append(f"Αντιληπτή κόπωση προπόνησης - RPE (1-10): {suffer:.0f}")

    return "\n".join(lines)


def format_activities_for_cell(day_activities: list[dict], details: dict) -> str:
    """Format all activities on a single day."""
    blocks = []
    for act in day_activities:
        detail = details.get(act.get("id"))
        blocks.append(format_activity_for_cell(act, detail))
    return "\n\n---\n\n".join(blocks)


def calculate_weekly_totals(row_activities: list[dict]) -> tuple[float, int, float, int, float, float, int, int]:
    """Calculate running, cycling, swimming, and strength totals for the week."""
    run_dist = 0.0
    run_time = 0
    bike_dist = 0.0
    bike_time = 0
    bike_elev = 0.0
    swim_dist_m = 0.0
    swim_time = 0
    strength_time = 0

    for act in row_activities:
        sport = act.get("sport_type") or act.get("type", "")
        moving_time = int(act.get("moving_time", 0))
        dist_m = float(act.get("distance", 0))
        elev = float(act.get("total_elevation_gain", 0))
        is_trainer = act.get("trainer", False)

        if sport in ["Run", "TrailRun"]:
            run_dist += dist_m / 1000.0
            run_time += moving_time
        elif sport in ["Ride", "VirtualRide", "GravelRide", "MountainBikeRide"]:
            if is_trainer and dist_m < 100 and moving_time > 0:
                dist_m = (moving_time / 3600.0) * 21000.0
            bike_dist += dist_m / 1000.0
            bike_time += moving_time
            bike_elev += elev
        elif sport == "Swim":
            dist_m = dist_m / 2.0
            swim_dist_m += dist_m
            swim_time += moving_time
        elif sport in ["WeightTraining", "Workout"]:
            strength_time += moving_time

    return run_dist, run_time, bike_dist, bike_time, bike_elev, swim_dist_m, swim_time, strength_time


def write_to_sheet(activities: list[dict], details: dict | None = None) -> None:
    """Write Strava and Garmin data into Google Sheets."""
    if not activities:
        print("No activities to write.")
        return

    if details is None:
        details = {}

    service = get_sheets_service()
    sheet = service.spreadsheets()

    print("\n📊 Syncing to Google Sheet...")

    # Fetch Column A to map week dates
    result = sheet.values().get(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"'{SHEET_NAME}'!A13:A120",
    ).execute()
    col_a = result.get("values", [])

    week_map = {}
    for idx, row in enumerate(col_a):
        row_num = 13 + idx
        val = row[0] if row else ""
        date_range = parse_date_range(val)
        if date_range:
            week_start, week_end = date_range
            week_map[week_start] = row_num

    # Group activities by day
    activities_by_day = {}
    for act in activities:
        raw_dt = act.get("start_date_local", "")
        if not raw_dt:
            continue
        act_date = datetime.fromisoformat(raw_dt.replace("Z", "+00:00")).date()
        if act_date not in activities_by_day:
            activities_by_day[act_date] = []
        activities_by_day[act_date].append(act)

    col_letters = ["B", "C", "D", "E", "F", "G", "H"]
    cell_info = []
    unique_week_rows = set()
    activities_by_row = {}

    for target_date, day_activities in activities_by_day.items():
        week_start = _get_week_start(target_date)
        if week_start not in week_map:
            print(f"  ⚠️  No matching week row for date {target_date} (week start: {week_start})")
            continue

        week_start_row = week_map[week_start]
        weekday_idx = target_date.weekday()
        col = col_letters[weekday_idx]

        unique_week_rows.add(week_start_row)
        if week_start_row not in activities_by_row:
            activities_by_row[week_start_row] = []
        activities_by_row[week_start_row].extend(day_activities)

        cell_info.append((target_date, day_activities, week_start_row, col))

    # Detect Layouts dynamically
    layout_check_ranges = [f"'{SHEET_NAME}'!A{r+4}" for r in unique_week_rows]
    layout_by_row = {}
    if layout_check_ranges:
        layout_res = sheet.values().batchGet(
            spreadsheetId=GOOGLE_SHEET_ID,
            ranges=layout_check_ranges,
        ).execute()

        for vr, r in zip(layout_res.get("valueRanges", []), unique_week_rows):
            vals = vr.get("values", [[]])
            val_a4 = vals[0][0].strip().upper() if vals and vals[0] else ""
            if "ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ" in val_a4 or "FEEDBACK" in val_a4:
                layout_by_row[r] = "new"
                print(f"  🔍 Row {r} detected as NEW block layout")
            else:
                layout_by_row[r] = "old"
                print(f"  🔍 Row {r} detected as OLD single-row layout")

    # Read existing cell contents
    read_ranges = []
    for target_date, day_activities, week_start_row, col in cell_info:
        layout = layout_by_row[week_start_row]
        target_row = (week_start_row + 4) if layout == "new" else week_start_row
        read_ranges.append(f"'{SHEET_NAME}'!{col}{target_row}")

    for r in unique_week_rows:
        layout = layout_by_row[r]
        if layout == "old":
            read_ranges.append(f"'{SHEET_NAME}'!A{r}")
        else:
            read_ranges.append(f"'{SHEET_NAME}'!B{r+5}")

    existing_values = {}
    if read_ranges:
        batch_get_res = sheet.values().batchGet(
            spreadsheetId=GOOGLE_SHEET_ID,
            ranges=list(set(read_ranges)),
        ).execute()
        for vr in batch_get_res.get("valueRanges", []):
            range_key = vr.get("range", "")
            vals = vr.get("values", [[]])
            val = vals[0][0] if vals and vals[0] else ""
            cell_match = re.search(r"([A-Z]+)(\d+)", range_key.split("!")[-1])
            if cell_match:
                k = f"{cell_match.group(1)}{cell_match.group(2)}"
                existing_values[k] = val

    def get_existing_value(col_str: str, row_int: int) -> str:
        return existing_values.get(f"{col_str}{row_int}", "")

    # Build updates for daily cells and weekly totals
    SEPARATOR = "\n\n── Strava Data ──\n"
    updates = []

    for target_date, day_activities, week_start_row, col in cell_info:
        layout = layout_by_row[week_start_row]
        target_row = (week_start_row + 4) if layout == "new" else week_start_row

        formatted = format_activities_for_cell(day_activities, details)
        existing = get_existing_value(col, target_row)

        if existing and existing.strip():
            if "── Strava Data ──" in existing:
                parts = existing.split("── Strava Data ──")
                coach_part = parts[0].rstrip()
                new_value = f"{coach_part}{SEPARATOR}{formatted}"
            else:
                new_value = f"{existing.rstrip()}{SEPARATOR}{formatted}"
            status = "📝+"
        else:
            new_value = formatted
            status = "📝 "

        updates.append(
            {
                "range": f"'{SHEET_NAME}'!{col}{target_row}",
                "values": [[new_value]],
            }
        )
        print(
            f"  {status} {target_date.strftime('%a %Y-%m-%d')} → cell {col}{target_row} "
            f"({len(day_activities)} activities, layout: {layout})"
        )

    # Initialize Garmin client if credentials are configured
    garmin_client = get_garmin_client()

    # Update weekly totals
    for r in unique_week_rows:
        layout = layout_by_row[r]
        row_activities = activities_by_row[r]
        run_dist, run_time, bike_dist, bike_time, bike_elev, swim_dist_m, swim_time, strength_time = calculate_weekly_totals(row_activities)

        # Determine week Monday and Sunday for Garmin query
        row_dates = [
            datetime.fromisoformat(a["start_date_local"].replace("Z", "+00:00")).date()
            for a in row_activities
        ]
        week_monday = _get_week_start(row_dates[0])
        week_sunday = week_monday + timedelta(days=6)

        # Query Garmin Health data if client is authenticated
        health_summary = None
        if garmin_client:
            try:
                health_summary = get_weekly_health_summary(week_monday, week_sunday, garmin_client)
            except Exception as e:
                print(f"  ⚠️  Failed to fetch Garmin data for week {week_monday}: {e}")

        sleep_val = f"{health_summary['total_sleep_h']:.1f}h" if health_summary and health_summary.get("total_sleep_h") else None
        rhr_val = f"{health_summary['avg_rhr']}" if health_summary and health_summary.get("avg_rhr") else None
        hrv_val = f"{health_summary['avg_hrv']}" if health_summary and health_summary.get("avg_hrv") else None

        if health_summary:
            print(f"  😴 Garmin Health ({week_monday} → {week_sunday}): Sleep={sleep_val or 'N/A'}, HRrest={rhr_val or 'N/A'}, HRV={hrv_val or 'N/A'}")

        # Format strings
        running_str = f" {run_dist:.2f} χλμ / {format_duration_short(run_time)}" if run_time > 0 else " 0.00 χλμ / 0λ"
        cycling_str = f" {bike_dist:.2f} χλμ / {format_duration_short(bike_time)}" if bike_time > 0 else " 0.00 χλμ / 0λ"
        if bike_time > 0 and bike_elev > 0:
            cycling_str += f" / {bike_elev:.0f}μ"
        swimming_str = f" {swim_dist_m:.0f}μ / {format_duration_short(swim_time)}" if swim_time > 0 else " 0μ / 0λ"
        strength_str = f" {format_duration_short(strength_time)}" if strength_time > 0 else " 0λ"

        total_time = sum(int(activity.get("moving_time", 0)) for activity in row_activities)
        total_str = f" {format_duration_short(total_time)}"

        if layout == "old":
            existing_a = get_existing_value("A", r)
            if not existing_a or not existing_a.strip():
                continue

            new_a = existing_a
            new_a = re.sub(r"^(\s*Τρέξιμο\s*:).*$", rf"\1{running_str}", new_a, flags=re.M)
            new_a = re.sub(r"^(\s*Ποδηλασία\s*:).*$", rf"\1{cycling_str}", new_a, flags=re.M)
            new_a = re.sub(r"^(\s*Κολύμβηση\s*:).*$", rf"\1{swimming_str}", new_a, flags=re.M)
            new_a = re.sub(r"^(\s*Κολύμπι\s*:).*$", rf"\1{swimming_str}", new_a, flags=re.M)

            # Insert / Update Strength Training (Ενδυνάμωση)
            if "Ενδυνάμωση" in new_a:
                new_a = re.sub(r"^(\s*Ενδυνάμωση\s*:).*$", rf"\1{strength_str}", new_a, flags=re.M)
            else:
                if "Κολύμβηση" in new_a:
                    new_a = re.sub(r"^(\s*Κολύμβηση\s*:.*)$", rf"\1\nΕνδυνάμωση :{strength_str}", new_a, flags=re.M)
                elif "Κολύμπι" in new_a:
                    new_a = re.sub(r"^(\s*Κολύμπι\s*:.*)$", rf"\1\nΕνδυνάμωση :{strength_str}", new_a, flags=re.M)
                elif "Αίσθηση κούρασης" in new_a:
                    new_a = re.sub(r"^(\s*Αίσθηση κούρασης.*)$", rf"Ενδυνάμωση :{strength_str}\n\1", new_a, flags=re.M)

            # Insert / Update Total Training Hours
            if "Συνολικές ώρες προπόνησης" in new_a:
                new_a = re.sub(r"^(\s*Συνολικές ώρες προπόνησης\s*:).*$", rf"\1{total_str}", new_a, flags=re.M)
            else:
                if "Ενδυνάμωση" in new_a:
                    new_a = re.sub(r"^(\s*Ενδυνάμωση\s*:.*)$", rf"\1\nΣυνολικές ώρες προπόνησης :{total_str}", new_a, flags=re.M)
                elif "Κολύμβηση" in new_a:
                    new_a = re.sub(r"^(\s*Κολύμβηση\s*:.*)$", rf"\1\nΣυνολικές ώρες προπόνησης :{total_str}", new_a, flags=re.M)
                elif "Κολύμπι" in new_a:
                    new_a = re.sub(r"^(\s*Κολύμπι\s*:.*)$", rf"\1\nΣυνολικές ώρες προπόνησης :{total_str}", new_a, flags=re.M)
                elif "Αίσθηση κούρασης" in new_a:
                    new_a = re.sub(r"^(\s*Αίσθηση κούρασης.*)$", rf"Συνολικές ώρες προπόνησης :{total_str}\n\1", new_a, flags=re.M)

            # Insert / Update Sleep / HRrest / HRV line
            sleep_hrv_str = f"Ύπνος {sleep_val or '__h'} • HRrest {rhr_val or '__'} • HRV {hrv_val or '__'}"
            if "Ύπνος" in new_a or "HRrest" in new_a:
                new_a = re.sub(r"^(\s*Ύπνος\s*.*?•\s*HRrest\s*.*?•\s*HRV\s*.*?)$", sleep_hrv_str, new_a, flags=re.M)
            else:
                if "Συνολικές ώρες προπόνησης" in new_a:
                    new_a = re.sub(r"^(\s*Συνολικές ώρες προπόνησης\s*:.*)$", rf"\1\n{sleep_hrv_str}", new_a, flags=re.M)
                elif "Αίσθηση κούρασης" in new_a:
                    new_a = re.sub(r"^(\s*Αίσθηση κούρασης.*)$", rf"{sleep_hrv_str}\n\1", new_a, flags=re.M)
                elif "Γενικές παρατηρήσεις" in new_a:
                    new_a = re.sub(r"^(\s*Γενικές παρατηρήσεις.*)$", rf"{sleep_hrv_str}\n\1", new_a, flags=re.M)
                else:
                    new_a = new_a.rstrip() + f"\n{sleep_hrv_str}\n"

            if new_a != existing_a:
                updates.append(
                    {
                        "range": f"'{SHEET_NAME}'!A{r}",
                        "values": [[new_a]],
                    }
                )
                print(f"  📝 Column A (Row {r}) totals updated (OLD format)")

        else:
            # layout == "new"
            target_row = r + 5
            existing_b = get_existing_value("B", target_row)

            running_time_val = format_duration_short(run_time)
            cycling_time_val = format_duration_short(bike_time)
            swimming_time_val = format_duration_short(swim_time)

            has_template = existing_b and "Τρέξιμο" in existing_b and "Ποδηλασία" in existing_b and "Κολύμβηση" in existing_b

            if not has_template:
                new_b = (
                    f"Τρέξιμο {run_dist:.2f} χλμ / {running_time_val} • Ποδηλασία {bike_dist:.2f} χλμ / {cycling_time_val} • Κολύμβηση {swim_dist_m:.0f} μ / {swimming_time_val}\n"
                    f"Κόπωση __/10 • Ύπνος {sleep_val or '__h'} • HRrest {rhr_val or '__'} • HRV {hrv_val or '__'} • Μυϊκή ενόχληση __/10 • Διάθεση __/10\n"
                    f"Σχόλιο εβδομάδας:"
                )
            else:
                new_b = existing_b
                new_b = re.sub(
                    r"(Τρέξιμο\s+)([^/]+?)(\s*χλμ\s*/\s*)([^•\n]+?)(\s*)(?=•|\n|$)",
                    rf"\g<1>{run_dist:.2f}\g<3>{running_time_val}\g<5>",
                    new_b
                )
                new_b = re.sub(
                    r"(Ποδηλασία\s+)([^/]+?)(\s*χλμ\s*/\s*)([^•\n]+?)(\s*)(?=•|\n|$)",
                    rf"\g<1>{bike_dist:.2f}\g<3>{cycling_time_val}\g<5>",
                    new_b
                )
                new_b = re.sub(
                    r"(Κολύμβηση\s+)([^/]+?)(\s*μ\s*/\s*)([^•\n]+?)(\s*)(?=•|\n|$)",
                    rf"\g<1>{swim_dist_m:.0f}\g<3>{swimming_time_val}\g<5>",
                    new_b
                )
                if sleep_val:
                    new_b = re.sub(
                        r"(Ύπνος\s+)([^•\n]+?)(\s*)(?=•|\n|$)",
                        rf"\g<1>{sleep_val}\g<3>",
                        new_b
                    )
                if rhr_val:
                    new_b = re.sub(
                        r"(HRrest\s+)([^•\n]+?)(\s*)(?=•|\n|$)",
                        rf"\g<1>{rhr_val}\g<3>",
                        new_b
                    )
                if hrv_val:
                    new_b = re.sub(
                        r"(HRV\s+)([^•\n]+?)(\s*)(?=•|\n|$)",
                        rf"\g<1>{hrv_val}\g<3>",
                        new_b
                    )

            if new_b != existing_b:
                updates.append(
                    {
                        "range": f"'{SHEET_NAME}'!B{target_row}",
                        "values": [[new_b]],
                    }
                )
                print(f"  📝 Column B (Row {target_row}) totals updated (NEW format)")

    if updates:
        body = {"valueInputOption": "RAW", "data": updates}
        result = (
            sheet.values()
            .batchUpdate(spreadsheetId=GOOGLE_SHEET_ID, body=body)
            .execute()
        )
        updated_count = result.get("totalUpdatedCells", len(updates))
        print(f"\n✅ Updated {updated_count} cells in Google Sheets!")
    else:
        print("\n✅ All cells are already up to date!")
