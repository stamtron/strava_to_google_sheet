"""
Strava Training Log Fetcher & Google Sheets Sync CLI.

Entry point for terminal-based activity logging and Google Sheets synchronization.
"""

import argparse
import time
from collections import defaultdict
from datetime import datetime

from src.integrations.strava import fetch_activities, fetch_activity_detail, get_access_token
from src.integrations.sheets import write_to_sheet


def format_duration(seconds: int) -> str:
    """Convert seconds to a human-readable duration string."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def format_pace(speed_mps: float, sport_type: str) -> str:
    """Convert m/s to min/km pace for running, min/100m for swimming, km/h for cycling."""
    if speed_mps <= 0:
        return "—"
    if sport_type == "Swim":
        pace_sec_per_100m = 100 / (speed_mps / 2.0) if speed_mps > 0 else 0
        mins = int(pace_sec_per_100m // 60)
        secs = int(pace_sec_per_100m % 60)
        return f"{mins}:{secs:02d} /100m"
    elif "Run" in sport_type or "Walk" in sport_type or "Hike" in sport_type:
        pace_sec_per_km = 1000 / speed_mps
        mins = int(pace_sec_per_km // 60)
        secs = int(pace_sec_per_km % 60)
        return f"{mins}:{secs:02d} /km"
    else:
        kmh = speed_mps * 3.6
        return f"{kmh:.1f} km/h"


def group_activities_by_date(activities: list[dict]) -> dict[str, list[dict]]:
    """Group activities by their local date (YYYY-MM-DD)."""
    grouped = defaultdict(list)
    for act in activities:
        raw_date = act.get("start_date_local", "")
        if raw_date:
            date_str = raw_date.split("T")[0]
            grouped[date_str].append(act)
    return dict(grouped)


def print_activities(activities: list[dict], details: dict[int, dict]) -> None:
    """Pretty-print fetched activities in terminal."""
    print("=" * 100)
    print(f"{'STRAVA TRAINING LOG':^100}")
    print("=" * 100)

    for act in activities:
        act_id = act["id"]
        sport = act.get("sport_type") or act.get("type", "Unknown")
        name = act.get("name", "Untitled")
        dist_km = (act.get("distance", 0) or 0) / 1000.0
        moving_time = act.get("moving_time", 0)
        speed = act.get("average_speed", 0)
        elev = act.get("total_elevation_gain", 0)
        raw_date = act.get("start_date_local", "")
        dt_str = raw_date.replace("T", " ")[:16] if raw_date else "Unknown date"

        print(f"\n  📌 {name}")
        print(f"     {dt_str}  •  {sport}")
        print("     " + "─" * 60)

        stats = [f"📏 {dist_km:.2f} km", f"⏱️  {format_duration(moving_time)}", f"🏎️  {format_pace(speed, sport)}"]
        if elev > 0:
            stats.append(f"⛰️  {elev:.0f}m gain")
        print("     " + " │ ".join(stats))

        hr_stats = []
        if act.get("average_heartrate"):
            hr_stats.append(f"❤️  Avg HR: {act['average_heartrate']:.0f} bpm")
        if act.get("max_heartrate"):
            hr_stats.append(f"Max HR: {act['max_heartrate']:.0f} bpm")

        detail = details.get(act_id, {})
        if detail.get("suffer_score"):
            hr_stats.append(f"Suffer Score: {detail['suffer_score']}")
        if hr_stats:
            print("     " + " │ ".join(hr_stats))

    print("\n" + "=" * 100)
    print(f"  Total activities: {len(activities)}")
    print("=" * 100 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Strava Training Log Fetcher & Google Sheets Sync")
    parser.add_argument("--count", type=int, default=30, help="Number of activities to fetch")
    parser.add_argument("--sheet", action="store_true", help="Sync activities to Google Sheets")
    args = parser.parse_args()

    print("🏃 Strava Training Log Fetcher\n")

    access_token = get_access_token()
    print(f"\n📥 Fetching last {args.count} activities from Strava...")
    activities = fetch_activities(access_token, per_page=args.count)

    print(f"📋 Fetching details for {len(activities)} activities", end="", flush=True)
    details = {}
    for activity in activities:
        act_id = activity["id"]
        try:
            details[act_id] = fetch_activity_detail(access_token, act_id)
            print(".", end="", flush=True)
            time.sleep(0.05)
        except Exception:
            pass
    print(" done!")

    print_activities(activities, details)

    if args.sheet:
        write_to_sheet(activities, details)


if __name__ == "__main__":
    main()
