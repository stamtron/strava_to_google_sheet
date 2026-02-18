"""
Strava Training Log Printer.

Connects to the Strava API and prints recent activities
with key metrics including heart rate, pace, calories, and more.
"""

import time
from datetime import datetime

import requests

from strava_auth import get_access_token

ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
ACTIVITY_DETAIL_URL = "https://www.strava.com/api/v3/activities"


def fetch_activities(access_token: str, per_page: int = 30) -> list[dict]:
    """Fetch recent activities from Strava."""
    response = requests.get(
        ACTIVITIES_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": per_page, "page": 1},
    )
    response.raise_for_status()
    return response.json()


def fetch_activity_detail(access_token: str, activity_id: int) -> dict:
    """Fetch detailed info for a single activity (includes description, calories, temp)."""
    response = requests.get(
        f"{ACTIVITY_DETAIL_URL}/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    return response.json()


def format_duration(seconds: int) -> str:
    """Convert seconds to a human-readable duration string."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def format_pace(speed_mps: float, sport_type: str) -> str:
    """Convert m/s to min/km pace for running, km/h for cycling."""
    if speed_mps <= 0:
        return "—"
    if "Run" in sport_type or "Walk" in sport_type or "Hike" in sport_type:
        # Pace in min/km
        pace_sec_per_km = 1000 / speed_mps
        mins = int(pace_sec_per_km // 60)
        secs = int(pace_sec_per_km % 60)
        return f"{mins}:{secs:02d} /km"
    else:
        # Speed in km/h
        kmh = speed_mps * 3.6
        return f"{kmh:.1f} km/h"


def print_activities(activities: list[dict], details: dict[int, dict]) -> None:
    """Print activities in a readable format with extended data."""
    if not activities:
        print("No activities found.")
        return

    print(f"\n{'='*100}")
    print(f"{'STRAVA TRAINING LOG':^100}")
    print(f"{'='*100}")

    for i, activity in enumerate(activities):
        act_id = activity["id"]
        detail = details.get(act_id, {})

        # Parse date
        start_date = datetime.fromisoformat(
            activity["start_date_local"].replace("Z", "+00:00")
        )
        date_str = start_date.strftime("%a %Y-%m-%d %H:%M")

        # Basic metrics
        name = activity.get("name", "Untitled")
        sport_type = activity.get("sport_type", activity.get("type", "Unknown"))
        distance_km = activity.get("distance", 0) / 1000
        duration = format_duration(int(activity.get("moving_time", 0)))
        elevation = activity.get("total_elevation_gain", 0)
        avg_speed = activity.get("average_speed", 0)
        pace = format_pace(avg_speed, sport_type)

        # Heart rate (from summary endpoint)
        has_hr = activity.get("has_heartrate", False)
        avg_hr = activity.get("average_heartrate")
        max_hr = activity.get("max_heartrate")

        # From detailed endpoint
        calories = detail.get("calories", None)
        avg_temp = detail.get("average_temp", None)
        description = detail.get("description", "")
        suffer_score = detail.get("suffer_score", None)

        # Print activity header
        print(f"\n  📌 {name}")
        print(f"     {date_str}  •  {sport_type}")
        print(f"     {'─'*60}")

        # Row 1: Distance, Duration, Pace, Elevation
        metrics = []
        if distance_km > 0:
            metrics.append(f"📏 {distance_km:.2f} km")
        metrics.append(f"⏱️  {duration}")
        if distance_km > 0:
            metrics.append(f"🏎️  {pace}")
        if elevation > 0:
            metrics.append(f"⛰️  {elevation:.0f}m gain")
        print(f"     {' │ '.join(metrics)}")

        # Row 2: Heart rate
        if has_hr and avg_hr:
            hr_str = f"❤️  Avg HR: {avg_hr:.0f} bpm │ Max HR: {max_hr:.0f} bpm"
            if suffer_score:
                hr_str += f" │ Suffer Score: {suffer_score}"
            print(f"     {hr_str}")

        # Row 3: Calories, Temperature
        extras = []
        if calories:
            extras.append(f"🔥 {calories:.0f} cal")
        if avg_temp is not None:
            extras.append(f"🌡️  {avg_temp}°C")
        if extras:
            print(f"     {' │ '.join(extras)}")

        # Row 4: Description
        if description and description.strip():
            desc_preview = description.strip().replace("\n", " ")
            if len(desc_preview) > 80:
                desc_preview = desc_preview[:77] + "..."
            print(f"     💬 {desc_preview}")

    print(f"\n{'='*100}")
    print(f"  Total activities: {len(activities)}")
    print(f"{'='*100}\n")


def main():
    print("🏃 Strava Training Log Fetcher\n")

    # Step 1: Authenticate
    access_token = get_access_token()

    # Step 2: Fetch summary activities
    print("\n📥 Fetching recent activities from Strava...")
    activities = fetch_activities(access_token)

    # Step 3: Fetch detailed info for each activity (for calories, temp, description)
    print(f"📋 Fetching details for {len(activities)} activities", end="", flush=True)
    details = {}
    for activity in activities:
        act_id = activity["id"]
        try:
            details[act_id] = fetch_activity_detail(access_token, act_id)
            print(".", end="", flush=True)
            time.sleep(0.1)  # Be nice to rate limits (200 req / 15 min)
        except Exception as e:
            print(f"\n  ⚠️  Could not fetch details for {act_id}: {e}")
    print(" done!")

    # Step 4: Print them
    print_activities(activities, details)


if __name__ == "__main__":
    main()
