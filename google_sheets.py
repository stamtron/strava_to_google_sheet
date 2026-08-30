"""
Google Sheets Integration Module.

Authenticates with Google Sheets API via OAuth2,
finds the correct cell by parsing week date ranges,
and writes formatted Strava training data.
"""

import os
import re
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from garmin_client import get_garmin_client, get_weekly_health_summary


load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
GSHEETS_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "gsheets_token.json")
SPREADSHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID", "1CaLk5B6r-o6vAj96zmYFUF55caKFCv75clbYG7Fq7cA"
)
SHEET_NAME = "Προπόνηση-Ανατροφοδότηση"

# Week blocks start at row 13, one row per week (cells are tall/merged visually)
FIRST_WEEK_ROW = 13
ROWS_PER_WEEK = 1
# The first week in the sheet starts on Monday 2025-09-01
FIRST_WEEK_START = date(2025, 9, 1)

# Day of week -> column letter (Monday=B, Tuesday=C, ..., Sunday=H)
DAY_COLUMNS = {
    0: "B",  # Monday
    1: "C",  # Tuesday
    2: "D",  # Wednesday
    3: "E",  # Thursday
    4: "F",  # Friday
    5: "G",  # Saturday
    6: "H",  # Sunday
}

# Sport type translations
SPORT_TYPE_GREEK = {
    "Run": "Τρέξιμο",
    "TrailRun": "Τρέξιμο (Trail)",
    "Ride": "Ποδήλατο",
    "VirtualRide": "Ποδήλατο (Virtual)",
    "WeightTraining": "Βάρη",
    "Workout": "Προπόνηση",
    "Walk": "Περπάτημα",
    "Hike": "Πεζοπορία",
    "Swim": "Κολύμβηση",
    "Yoga": "Yoga",
}


def get_sheets_service():
    """Authenticate and return Google Sheets API service."""
    creds = None

    if os.path.exists(GSHEETS_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GSHEETS_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄 Refreshing Google Sheets token...")
            try:
                creds.refresh(Request())
            except RefreshError:
                print("⚠️ Refresh token expired or revoked. Forcing re-authentication...")
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_FILE}. Download it from Google Cloud Console."
                )
            print("🌐 Opening browser for Google authorization...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)

        with open(GSHEETS_TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
        print(f"✅ Google token saved to {GSHEETS_TOKEN_FILE}")

    return build("sheets", "v4", credentials=creds)


def _get_week_start(target_date: date) -> date:
    """Get the Monday of the week containing the target date."""
    return target_date - timedelta(days=target_date.weekday())


def parse_date_range(text: str) -> tuple[date, date] | None:
    """Parse a date range string from the coaching sheet (e.g. '13-19/7/\'26' or '24/8–30/8/2026')."""
    if not text:
        return None
    # Clean up text: take only the first line, strip whitespace
    first_line = text.split('\n')[0].strip()
    # Normalize spaces
    first_line = first_line.replace(' ', '')
    # Normalize dashes (hyphen, en-dash, em-dash)
    first_line = first_line.replace('–', '-').replace('—', '-')
    # Normalize quotes
    first_line = first_line.replace('’', "'").replace('‘', "'").replace('`', "'")
    
    # Try to match: Day-Day/Month/'Year or Day-Day/Month/Year
    # e.g., 1-7/9/'25 or 10-16/3/2025
    m1 = re.match(r"^(\d+)-(\d+)/(\d+)/'?(\d{2}|\d{4})$", first_line)
    if m1:
        start_day = int(m1.group(1))
        end_day = int(m1.group(2))
        month = int(m1.group(3))
        yr_val = int(m1.group(4))
        year = 2000 + yr_val if yr_val < 100 else yr_val
        if start_day > end_day:
            start_month = month - 1 if month > 1 else 12
            start_year = year if month > 1 else year - 1
        else:
            start_month = month
            start_year = year
        try:
            return date(start_year, start_month, start_day), date(year, month, end_day)
        except ValueError:
            return None
        
    # Try to match: Day/Month-Day/Month/'Year or Day/Month-Day/Month/Year
    # e.g., 29/9-5/10/'25 or 24/8-30/8/2026
    m2 = re.match(r"^(\d+)/(\d+)-(\d+)/(\d+)/'?(\d{2}|\d{4})$", first_line)
    if m2:
        start_day = int(m2.group(1))
        start_month = int(m2.group(2))
        end_day = int(m2.group(3))
        end_month = int(m2.group(4))
        yr_val = int(m2.group(5))
        year = 2000 + yr_val if yr_val < 100 else yr_val
        start_year = year if start_month <= end_month else year - 1
        try:
            return date(start_year, start_month, start_day), date(year, end_month, end_day)
        except ValueError:
            return None
        
    # Try to match: Day/Month/'Year-Day/Month/'Year or Day/Month/Year-Day/Month/Year
    # e.g., 29/12/'25-4/1/'26 or 29/12/2025-4/1/2026
    m3 = re.match(r"^(\d+)/(\d+)/'?(\d{2}|\d{4})-(\d+)/(\d+)/'?(\d{2}|\d{4})$", first_line)
    if m3:
        start_day = int(m3.group(1))
        start_month = int(m3.group(2))
        start_yr_val = int(m3.group(3))
        start_year = 2000 + start_yr_val if start_yr_val < 100 else start_yr_val
        end_day = int(m3.group(4))
        end_month = int(m3.group(5))
        end_yr_val = int(m3.group(6))
        end_year = 2000 + end_yr_val if end_yr_val < 100 else end_yr_val
        try:
            return date(start_year, start_month, start_day), date(end_year, end_month, end_day)
        except ValueError:
            return None

    return None


def build_row_mapping(service) -> dict[date, int]:
    """Fetch Column A from Google Sheet and map week start dates (Mondays) to row numbers."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A:A"
    ).execute()
    values = result.get('values', [])
    
    mapping = {}
    for i, val_list in enumerate(values):
        row_num = i + 1
        val = val_list[0] if val_list else ""
        parsed = parse_date_range(val)
        if parsed:
            start_date, end_date = parsed
            # Normalize to the Monday of that week
            monday = start_date - timedelta(days=start_date.weekday())
            mapping[monday] = row_num
    return mapping


def _calculate_row_for_date(target_date: date, mapping: dict[date, int] = None) -> int:
    """
    Calculate the sheet row for a given date.
    
    Uses dynamic mapping from Column A if available, otherwise falls back to
    the mathematical calculation based on FIRST_WEEK_START.
    """
    week_start = _get_week_start(target_date)

    if mapping is not None:
        row = mapping.get(week_start)
        if row is not None:
            return row
        else:
            print(f"  ⚠️  Week starting {week_start} not found in dynamic mapping. Falling back to math.")

    # Fallback mathematical calculation
    first_week_start = _get_week_start(FIRST_WEEK_START)
    weeks_diff = (week_start - first_week_start).days // 7
    row = FIRST_WEEK_ROW + (weeks_diff * ROWS_PER_WEEK)
    return row


def _day_to_column(target_date: date) -> str:
    """Map a date's day-of-week to the corresponding sheet column."""
    return DAY_COLUMNS[target_date.weekday()]


def format_pace(speed_mps: float, sport_type: str) -> str:
    """Convert m/s to min/km pace for running, min/100m for swimming, km/h for cycling."""
    if speed_mps <= 0:
        return ""
    if sport_type == "Swim":
        pace_sec_per_100m = 100 / speed_mps
        mins = int(pace_sec_per_100m // 60)
        secs = int(pace_sec_per_100m % 60)
        return f"{mins}:{secs:02d} /100μ"
    elif "Run" in sport_type or "Walk" in sport_type or "Hike" in sport_type:
        pace_sec_per_km = 1000 / speed_mps
        mins = int(pace_sec_per_km // 60)
        secs = int(pace_sec_per_km % 60)
        return f"{mins}:{secs:02d} /χλμ"
    else:
        kmh = speed_mps * 3.6
        return f"{kmh:.1f} χλμ/ω"


def format_duration(seconds: int) -> str:
    """Convert seconds to a human-readable duration string."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}ω {minutes}λ {secs}δ"
    return f"{minutes}λ {secs}δ"


def format_duration_short(seconds: int) -> str:
    """Convert seconds to a short duration string (hours/minutes, no seconds)."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ω {minutes}λ"
    return f"{minutes}λ"


def calculate_weekly_totals(activities: list[dict]) -> tuple[float, int, float, int, float, float, int, int]:
    """
    Calculate weekly totals for Running, Cycling, Swimming, and Strength activities.
    Returns: (run_dist, run_time, bike_dist, bike_time, bike_elev, swim_dist_m, swim_time, strength_time)
    """
    run_dist = 0.0
    run_time = 0
    bike_dist = 0.0
    bike_time = 0
    bike_elev = 0.0
    swim_dist_m = 0.0
    swim_time = 0
    strength_time = 0

    for activity in activities:
        sport_type = activity.get("sport_type", activity.get("type", "Unknown"))
        dist = activity.get("distance", 0)
        moving_time = int(activity.get("moving_time", 0))
        elev = activity.get("total_elevation_gain", 0)

        if sport_type in ["Run", "TrailRun"]:
            run_dist += dist / 1000.0
            run_time += moving_time
        elif sport_type in ["Ride", "VirtualRide"]:
            d_km = dist / 1000.0
            if activity.get("trainer", False) and d_km < 0.1:
                d_km = (moving_time / 3600.0) * 21.0
            bike_dist += d_km
            bike_time += moving_time
            bike_elev += elev
        elif sport_type == "Swim":
            # Swimming distance is divided by 2
            swim_dist_m += (dist / 2.0)
            swim_time += moving_time
        elif sport_type == "WeightTraining":
            strength_time += moving_time

    return run_dist, run_time, bike_dist, bike_time, bike_elev, swim_dist_m, swim_time, strength_time


def format_activities_for_cell(activities: list[dict], details: dict) -> str:
    """
    Format all activities for a single day into one multi-line text block.
    Matches the existing Greek format used in the coaching sheet.
    """
    blocks = []

    for activity in activities:
        act_id = activity["id"]
        detail = details.get(act_id, {})

        sport_type = activity.get("sport_type", activity.get("type", "Unknown"))
        sport_greek = SPORT_TYPE_GREEK.get(sport_type, sport_type)
        name = activity.get("name", "")
        moving_time = int(activity.get("moving_time", 0))
        
        distance_km = activity.get("distance", 0) / 1000.0
        avg_speed = activity.get("average_speed", 0)

        # Apply adjustments
        if sport_type == "Swim":
            distance_km = distance_km / 2.0
            avg_speed = avg_speed / 2.0
        elif sport_type in ["Ride", "VirtualRide"]:
            if activity.get("trainer", False) and distance_km < 0.1:
                distance_km = (moving_time / 3600.0) * 21.0
                avg_speed = 21.0 / 3.6
        has_hr = activity.get("has_heartrate", False)
        avg_hr = activity.get("average_heartrate")
        max_hr = activity.get("max_heartrate")
        elevation = activity.get("total_elevation_gain", 0)
        calories = detail.get("calories")
        avg_temp = detail.get("average_temp")
        suffer_score = detail.get("suffer_score")
        description = detail.get("description", "")

        lines = []
        lines.append(f"{sport_greek}: {name}")

        if distance_km > 0.01:
            lines.append(f"Απόσταση: {distance_km:.2f} χλμ")

        lines.append(f"Συνολικός χρόνος: {format_duration(moving_time)}")

        if distance_km > 0.01 and avg_speed > 0:
            pace = format_pace(avg_speed, sport_type)
            lines.append(f"Μέσος ρυθμός: {pace}")

        if has_hr and avg_hr:
            lines.append(f"Μέσοι καρδιακοί παλμοί: {avg_hr:.0f}")
            if max_hr:
                lines.append(f"Μέγιστοι καρδιακοί παλμοί: {max_hr:.0f}")

        if elevation > 0:
            lines.append(f"Υψομετρική διαφορά: {elevation:.0f}μ")

        if calories:
            lines.append(f"Θερμίδες: {calories:.0f}")

        if avg_temp is not None:
            lines.append(f"Θερμοκρασία: {avg_temp}°C")

        if suffer_score:
            # Normalize Strava Suffer Score (0-250+) to 1-10 RPE scale
            rpe = max(1, min(10, round(suffer_score / 12)))
            lines.append(f"Αντιληπτή κόπωση προπόνησης - RPE (1-10): {rpe}")

        if description and description.strip():
            lines.append(f"Σχόλια: {description.strip()}")

        blocks.append("\n".join(lines))

    return "\n---\n".join(blocks)


def write_to_sheet(
    activities_by_date: dict[date, list[dict]],
    details: dict[int, dict],
) -> None:
    """
    Write training data to the Google Sheet.

    Reads existing cell content first and APPENDS the Strava data below it,
    preserving any coach instructions already in the cell. Also calculates
    weekly totals and updates the sheet based on the format (old single-row
    or new 7-row block layout).
    """
    from collections import defaultdict
    service = get_sheets_service()
    sheet = service.spreadsheets()

    # Build list of cells we need to write to
    try:
        mapping = build_row_mapping(service)
    except Exception as e:
        print(f"  ⚠️  Could not load dynamic row mapping: {e}. Using mathematical mapping fallback.")
        mapping = None

    # Step 1: Compute week start rows for all target dates
    week_rows = set()
    for target_date in activities_by_date:
        row = _calculate_row_for_date(target_date, mapping)
        week_rows.add(row)
    unique_week_rows = sorted(list(week_rows))

    if not unique_week_rows:
        print("  ℹ️  No activities to write.")
        return

    # Step 2: Dynamically detect layout for each unique week row
    layout_check_ranges = []
    for r in unique_week_rows:
        layout_check_ranges.append(f"'{SHEET_NAME}'!A{r}")
        layout_check_ranges.append(f"'{SHEET_NAME}'!A{r+4}")

    try:
        layout_check_result = (
            sheet.values()
            .batchGet(spreadsheetId=SPREADSHEET_ID, ranges=layout_check_ranges)
            .execute()
        )
    except Exception as e:
        print(f"  ⚠️  Failed to fetch layout verification cells: {e}. Defaulting to old layout.")
        layout_check_result = {}

    layout_values = {}
    for vr in layout_check_result.get("valueRanges", []):
        range_key = vr.get("range", "")
        values = vr.get("values", [[]])
        layout_values[range_key] = values[0][0] if values and values[0] else ""

    def get_layout_check_val(col_letter: str, row_num: int) -> str:
        cell_coord = f"{col_letter}{row_num}"
        for key, val in layout_values.items():
            parts = key.split('!')
            if len(parts) > 1:
                coord_part = parts[1].replace('$', '')
                if cell_coord in coord_part:
                    return val
        return ""

    layout_by_row = {}
    for r in unique_week_rows:
        val_r_plus_4 = get_layout_check_val("A", r + 4).strip().upper()
        if "ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ" in val_r_plus_4 or "FEEDBACK" in val_r_plus_4:
            layout_by_row[r] = "new"
            print(f"  🔍 Row {r} detected as NEW block layout")
        else:
            layout_by_row[r] = "old"
            print(f"  🔍 Row {r} detected as OLD single-row layout")

    # Step 3: Build list of daily cell info and total activities per row
    cell_info = []
    activities_by_row = defaultdict(list)

    for target_date, day_activities in sorted(activities_by_date.items()):
        week_start_row = _calculate_row_for_date(target_date, mapping)
        layout = layout_by_row[week_start_row]
        col = _day_to_column(target_date)

        if layout == "new":
            target_row = week_start_row + 4
        else:
            target_row = week_start_row

        cell_ref = f"'{SHEET_NAME}'!{col}{target_row}"
        cell_info.append((target_date, day_activities, target_row, col, cell_ref, week_start_row, layout))
        activities_by_row[week_start_row].extend(day_activities)

    # Step 4: Batch-read existing content from daily cells and weekly totals cells
    ranges = [info[4] for info in cell_info]
    for r in unique_week_rows:
        layout = layout_by_row[r]
        if layout == "new":
            ranges.append(f"'{SHEET_NAME}'!B{r+5}")
        else:
            ranges.append(f"'{SHEET_NAME}'!A{r}")

    # Deduplicate ranges
    ranges = list(set(ranges))

    existing_result = (
        sheet.values()
        .batchGet(spreadsheetId=SPREADSHEET_ID, ranges=ranges)
        .execute()
    )

    existing_values = {}
    for vr in existing_result.get("valueRanges", []):
        range_key = vr.get("range", "")
        values = vr.get("values", [[]])
        existing_values[range_key] = values[0][0] if values and values[0] else ""

    def get_existing_value(col_letter: str, row_num: int) -> str:
        cell_coord = f"{col_letter}{row_num}"
        for key, val in existing_values.items():
            parts = key.split('!')
            if len(parts) > 1:
                coord_part = parts[1].replace('$', '')
                if ':' in coord_part:
                    coord_parts = coord_part.split(':')
                    if cell_coord in coord_parts:
                        return val
                elif coord_part == cell_coord:
                    return val
        return ""

    # Step 5: Build updates for daily cells and weekly totals
    SEPARATOR = "\n\n── Strava Data ──\n"
    updates = []

    # Update daily cells (B-H of either row R or row R+4)
    for target_date, day_activities, target_row, col, cell_ref, week_start_row, layout in cell_info:
        formatted = format_activities_for_cell(day_activities, details)
        existing = get_existing_value(col, target_row)

        if existing and existing.strip():
            # Check if Strava data was already appended (avoid duplicates)
            if "── Strava Data ──" in existing:
                # Replace old Strava data section
                parts = existing.split("── Strava Data ──")
                new_content = parts[0].rstrip() + SEPARATOR + formatted
            else:
                new_content = existing.rstrip() + SEPARATOR + formatted
        else:
            new_content = formatted

        updates.append(
            {
                "range": cell_ref,
                "values": [[new_content]],
            }
        )

        status = "📝" if not existing else "📝+"
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

        # Overall training time (sum of all activities in that week)
        total_time = sum(int(activity.get("moving_time", 0)) for activity in row_activities)
        total_str = f" {format_duration_short(total_time)}"

        if layout == "old":
            existing_a = get_existing_value("A", r)
            if not existing_a or not existing_a.strip():
                continue

            # Perform replacements for targets in Column A (Old Format)
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

            # Insert / Update Total Training Hours (Συνολικές ώρες προπόνησης)
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
                # Fallback / create from default template
                new_b = (
                    f"Τρέξιμο {run_dist:.2f} χλμ / {running_time_val} • Ποδηλασία {bike_dist:.2f} χλμ / {cycling_time_val} • Κολύμβηση {swim_dist_m:.0f} μ / {swimming_time_val}\n"
                    f"Κόπωση __/10 • Ύπνος {sleep_val or '__h'} • HRrest {rhr_val or '__'} • HRV {hrv_val or '__'} • Μυϊκή ενόχληση __/10 • Διάθεση __/10\n"
                    f"Σχόλιο εβδομάδας:"
                )
            else:
                # Update in-place using robust regex replacements
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
            .batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body)
            .execute()
        )
        print(f"\n✅ Updated {result.get('totalUpdatedCells', 0)} cells in Google Sheets!")
    else:
        print("\nℹ️ No updates needed.")


def verify_cell_mapping(target_date: date) -> None:
    """Debug helper: print which cell a date maps to."""
    mapping = None
    try:
        service = get_sheets_service()
        mapping = build_row_mapping(service)
    except Exception:
        pass
    row = _calculate_row_for_date(target_date, mapping)
    col = _day_to_column(target_date)
    week_start = _get_week_start(target_date)
    print(
        f"  Date {target_date} ({target_date.strftime('%A')}) "
        f"→ week of {week_start} → cell {col}{row}"
    )
