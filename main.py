"""
Strava Training Log Fetcher & Google Sheets Sync CLI.

Entry point for terminal-based activity logging and Google Sheets synchronization.
"""

import argparse
import time
from collections import defaultdict

from src.config import STRAVA_DETAIL_DELAY_SEC
from src.formatting import corrected_distance_and_speed, format_duration, format_pace
from src.integrations.strava import (
    StravaAuthRequired,
    StravaNetworkError,
    StravaRateLimitError,
    fetch_activities,
    fetch_details_for_activities,
    get_access_token,
)
from src.integrations.sheets import write_to_sheet
from src.integrations.strava_backfill import backfill_all
from src.storage.activity_store import init_db


def group_activities_by_date(activities: list[dict]) -> dict[str, list[dict]]:
    """Group activities by their local date (YYYY-MM-DD)."""
    grouped = defaultdict(list)
    for act in activities:
        raw_date = act.get("start_date_local", "")
        if raw_date:
            date_str = raw_date.split("T")[0]
            grouped[date_str].append(act)
    return dict(grouped)


def print_activities(activities: list[dict], details: dict) -> None:
    """Pretty-print fetched activities in terminal."""
    print("=" * 100)
    print(f"{'STRAVA TRAINING LOG':^100}")
    print("=" * 100)

    for act in activities:
        act_id = act["id"]
        sport = act.get("sport_type") or act.get("type", "Unknown")
        name = act.get("name", "Untitled")
        # Applies the swim divisor and the indoor-trainer distance estimate.
        dist_m, speed = corrected_distance_and_speed(act)
        dist_km = dist_m / 1000.0
        moving_time = act.get("moving_time", 0)
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

        detail = details.get(act_id) or details.get(str(act_id)) or {}
        if detail.get("suffer_score"):
            hr_stats.append(f"Suffer Score: {detail['suffer_score']}")
        if hr_stats:
            print("     " + " │ ".join(hr_stats))

    print("\n" + "=" * 100)
    print(f"  Total activities: {len(activities)}")
    print("=" * 100 + "\n")


def run_backfill(access_token: str, resume: bool = True) -> int:
    """
    Import the full Strava activity history into the local store.

    First run walks every page of the athlete's history, so it takes a while and
    may hit the rate limit; the cursor is persisted, so simply re-running picks
    up where it stopped.
    """
    conn = init_db()
    try:
        print("📚 Backfilling full Strava history into the local store...")
        result = backfill_all(access_token, conn, resume=resume, progress=True)
    finally:
        conn.close()

    print(
        f"\n  status:     {result['status']}"
        f"\n  pages:      {result['pages_fetched']}"
        f"\n  stored:     {result['activities_stored']}"
        f"\n  total:      {result['total_activities']}"
        f"\n  date range: {result['oldest']} → {result['newest']}"
    )
    if result.get("error"):
        print(f"  error:      {result['error']}")
        print(f"  Re-run --backfill to resume from page {result['next_page']}.")
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Strava Training Log Fetcher & Google Sheets Sync")
    parser.add_argument("--count", type=int, default=200, help="Number of activities to fetch")
    parser.add_argument("--sheet", action="store_true", help="Sync activities to Google Sheets")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Import the full Strava history into the local store, then exit",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="With --backfill, restart the import from page 1 instead of the saved cursor",
    )
    parser.add_argument(
        "--telegram-today",
        action="store_true",
        help="Read today's planned workout from Google Sheets, add Athens weather & tip, and send to Telegram",
    )
    parser.add_argument(
        "--telegram-next-day",
        action="store_true",
        help="Read tomorrow's planned workout from Google Sheets, add Athens weather & tip, and send to Telegram",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Specific date (YYYY-MM-DD) to send briefing for",
    )
    parser.add_argument(
        "--whatsapp-next-day",
        action="store_true",
        help="[Deprecated] Use --telegram-next-day instead",
    )
    parser.add_argument(
        "--telegram-bot",
        action="store_true",
        help="Run the interactive two-way Telegram bot polling worker",
    )
    parser.add_argument(
        "--sync-workouts",
        action="store_true",
        help="Sync this week's running and cycling workouts from Google Sheets to Garmin Connect",
    )
    parser.add_argument(
        "--preview-workouts",
        action="store_true",
        help="Preview detected running and cycling workouts from Google Sheets without uploading to Garmin",
    )
    parser.add_argument(
        "--week-offset",
        type=int,
        default=0,
        help="Week offset for workout sync (0 = current week, 1 = next week)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without sending external notifications or writing data",
    )
    args = parser.parse_args()

    # Garmin Workout Sync & Preview CLI
    if args.preview_workouts or args.sync_workouts:
        from src.integrations.garmin_workouts import preview_week_workouts, sync_week_workouts_to_garmin
        if args.preview_workouts or args.dry_run:
            print(f"\n🔍 Previewing Running & Cycling workouts (Week offset: {args.week_offset})...\n")
            preview = preview_week_workouts(week_offset=args.week_offset)
            print(f"📅 Week: {preview['week_start']} → {preview['week_end']} (Layout: {preview['layout']})")
            print(f"Found {preview['workouts_found']} target workouts (Running & Cycling only):\n")
            for w in preview["workouts"]:
                print(f"  📌 [{w['date']} - {w['day_name']}] {w['sport'].upper()}: {w['workout_name']}")
                print(f"     Steps ({len(w.get('steps', []))}):")
                for s in w.get("steps", []):
                    c_info = f"{s.get('condition_value')} {s.get('condition_type')}"
                    t_info = f"target: {s.get('target_type')}"
                    if s.get("target_value_low"):
                        t_info += f" ({s.get('target_value_low')} - {s.get('target_value_high')})"
                    print(f"       • {s.get('step_type').capitalize():<10} | {c_info:<20} | {t_info}")
                print(f"     Notes: {w.get('raw_coach_text')[:100].replace(chr(10), ' ')}...\n")
            return 0

        print(f"\n🚀 Syncing Running & Cycling workouts to Garmin Connect (Week offset: {args.week_offset})...\n")
        sync_res = sync_week_workouts_to_garmin(week_offset=args.week_offset, dry_run=False)
        print(f"📅 Week: {sync_res['week_start']} → {sync_res['week_end']}")
        print(f"Synced {len(sync_res['results'])} workouts to Garmin Connect Calendar:")
        for r in sync_res["results"]:
            status_icon = "✅" if r.get("status") == "scheduled" else "❌"
            w_id = r.get("workout_id", "N/A")
            print(f"  {status_icon} [{r['date']}] {r['sport'].upper()} - {r['workout_name']} (ID: {w_id}, status: {r.get('status')})")
        return 0

    # Standalone interactive Telegram bot listener
    if args.telegram_bot:
        from src.integrations.telegram import poll_telegram_updates, process_incoming_update, run_daily_dispatch_check
        print("🤖 Starting interactive Telegram bot worker (Ctrl+C to stop)...")
        offset = 0
        try:
            while True:
                run_daily_dispatch_check()
                offset, updates = poll_telegram_updates(offset=offset, timeout=10)
                for u in updates:
                    process_incoming_update(u)
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n👋 Telegram bot stopped.")
            return 0

    # Telegram Workout Dispatcher
    if args.telegram_today or args.telegram_next_day or args.whatsapp_next_day or (args.date and not args.sheet):
        if args.whatsapp_next_day:
            print("  ℹ️  Note: WhatsApp is deprecated, routing to Telegram dispatcher.")

        from datetime import date, timedelta
        from src.integrations.sheets import get_planned_workout_for_date
        from src.integrations.weather import get_weather_for_date
        from src.integrations.telegram import format_next_day_brief, send_telegram_message

        if args.date:
            target_date = date.fromisoformat(args.date)
        elif args.telegram_today:
            target_date = date.today()
        else:
            target_date = date.today() + timedelta(days=1)

        label = "Today's" if target_date == date.today() else "Next-Day"
        print(f"✈️ Preparing {label} Workout Brief for {target_date}...")

        workout_info = get_planned_workout_for_date(target_date)
        weather_info = get_weather_for_date(target_date)

        # `reason` is set only when the plan could not be determined; a genuine
        # rest day comes back empty with no reason.
        lookup_error = workout_info.get("reason")
        if lookup_error:
            print(f"  ⚠️  Could not read the planned workout: {lookup_error}")

        tip = "Keep easy aerobic pace in Zone 2 for optimal recovery and mitochondrial adaptation."
        if weather_info and (weather_info.get("precipitation_mm") or 0) > 2.0:
            tip = "Rain forecast; check tire pressure for wet roads or consider indoor trainer."
        elif weather_info and (weather_info.get("temp_max_c") or 0) > 32:
            tip = "High heat expected; hydrate well and start early morning."

        brief = format_next_day_brief(
            target_date=target_date,
            workout_text=workout_info.get("workout_text", ""),
            weather_info=weather_info,
            coach_tip=tip,
            lookup_error=lookup_error,
        )

        if args.dry_run:
            print("\n📋 [Telegram Next-Day Preview (Dry Run)]")
            print(brief)
            return 0

        res = send_telegram_message(brief)
        print(f"  Result: {res.get('detail')}")
        return 0 if res.get("success") else 1

    print("🏃 Strava Training Log Fetcher\n")

    try:
        access_token = get_access_token(interactive=True)

        if args.backfill:
            return run_backfill(access_token, resume=not args.no_resume)

        print(f"\n📥 Fetching last {args.count} activities from Strava...")
        activities = fetch_activities(access_token, per_page=args.count)

        print(f"📋 Fetching details for {len(activities)} activities", end="", flush=True)
        details = fetch_details_for_activities(
            access_token,
            activities,
            delay_sec=STRAVA_DETAIL_DELAY_SEC,
            progress=True,
        )
        print(" done!")
    except StravaAuthRequired as e:
        print(f"\n❌ Strava authorization failed: {e}")
        return 1
    except StravaRateLimitError as e:
        print(f"\n❌ {e}")
        return 1
    except StravaNetworkError as e:
        print(f"\n❌ {e}")
        print("   Check your internet connection or proxy settings and try again.")
        return 1

    print_activities(activities, details)

    if args.sheet:
        write_to_sheet(activities, details)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
