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
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

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
            creds.refresh(Request())
        else:
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


def _calculate_row_for_date(target_date: date) -> int:
    """
    Calculate the sheet row for a given date.

    The sheet has a 14-row block per week starting at row 13.
    Week 1 starts on 2025-09-01 (Monday).
    """
    week_start = _get_week_start(target_date)
    first_week_start = _get_week_start(FIRST_WEEK_START)

    weeks_diff = (week_start - first_week_start).days // 7
    row = FIRST_WEEK_ROW + (weeks_diff * ROWS_PER_WEEK)
    return row


def _day_to_column(target_date: date) -> str:
    """Map a date's day-of-week to the corresponding sheet column."""
    return DAY_COLUMNS[target_date.weekday()]


def format_pace(speed_mps: float, sport_type: str) -> str:
    """Convert m/s to min/km pace for running, km/h for cycling."""
    if speed_mps <= 0:
        return ""
    if "Run" in sport_type or "Walk" in sport_type or "Hike" in sport_type:
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
        distance_km = activity.get("distance", 0) / 1000
        moving_time = int(activity.get("moving_time", 0))
        avg_speed = activity.get("average_speed", 0)
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
    preserving any coach instructions already in the cell.
    """
    service = get_sheets_service()
    sheet = service.spreadsheets()

    # Build list of cells we need to write to
    cell_info = []
    for target_date, day_activities in sorted(activities_by_date.items()):
        row = _calculate_row_for_date(target_date)
        col = _day_to_column(target_date)
        cell_ref = f"'{SHEET_NAME}'!{col}{row}"
        cell_info.append((target_date, day_activities, row, col, cell_ref))

    if not cell_info:
        print("  ℹ️  No activities to write.")
        return

    # Batch-read existing content from all target cells
    ranges = [info[4] for info in cell_info]
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

    # Build updates: append Strava data below existing content
    SEPARATOR = "\n\n── Strava Data ──\n"
    updates = []
    for target_date, day_activities, row, col, cell_ref in cell_info:
        formatted = format_activities_for_cell(day_activities, details)

        # Find existing content (range key format may differ slightly)
        existing = ""
        for key, val in existing_values.items():
            if f"{col}{row}" in key:
                existing = val
                break

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
            f"  {status} {target_date.strftime('%a %Y-%m-%d')} → cell {col}{row} "
            f"({len(day_activities)} activities)"
        )

    body = {"valueInputOption": "RAW", "data": updates}
    result = (
        sheet.values()
        .batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body)
        .execute()
    )

    print(f"\n✅ Updated {result.get('totalUpdatedCells', 0)} cells in Google Sheets!")


def verify_cell_mapping(target_date: date) -> None:
    """Debug helper: print which cell a date maps to."""
    row = _calculate_row_for_date(target_date)
    col = _day_to_column(target_date)
    week_start = _get_week_start(target_date)
    print(
        f"  Date {target_date} ({target_date.strftime('%A')}) "
        f"→ week of {week_start} → cell {col}{row}"
    )
