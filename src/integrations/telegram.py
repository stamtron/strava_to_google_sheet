"""
Telegram Notification Dispatcher & Interactive Two-Way Bot.

Sends training alerts, daily briefs, and next-day workouts to athlete's Telegram
via Telegram Bot API, and listens for interactive commands (/today, /tomorrow, /sync,
/stats, /gear, /coach).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests

from src.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_DAILY_DISPATCH_TIME,
)
from src.analytics.weather_pace import calculate_weather_pace_adjustment, parse_pace_string_to_sec

logger = logging.getLogger(__name__)

GREEK_DAY_NAMES = {
    0: "Δευτέρα",
    1: "Τρίτη",
    2: "Τετάρτη",
    3: "Πέμπτη",
    4: "Παρασκευή",
    5: "Σάββατο",
    6: "Κυριακή",
}

# State tracker to prevent duplicate daily auto-dispatches on the same day
_LAST_DISPATCHED_DATE: str | None = None


def send_telegram_message(message: str, chat_id: str | None = None) -> dict:
    """
    Dispatch a message to athlete's Telegram via Telegram Bot API.

    Falls back to plain text if Markdown entity parsing fails, and falls back to
    console print if credentials are not configured.

    Returns {'success': bool, 'provider': str, 'detail': str}.
    """
    text = (message or "").strip()
    if not text:
        return {"success": False, "provider": "telegram", "detail": "Empty message."}

    token = (TELEGRAM_BOT_TOKEN or "").strip()
    target_chat_id = (chat_id or TELEGRAM_CHAT_ID or "").strip()

    if not token or not target_chat_id:
        logger.info("Telegram bot token or chat ID not configured; printing to console.")
        print(f"\n✈️ [Telegram (Dry Run)]\n{text}\n")
        return {
            "success": True,
            "provider": "telegram (dry-run)",
            "detail": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID unset; printed to console.",
        }

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Attempt 1: Send with Markdown formatting
    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=10.0)
        if resp.status_code == 200:
            return {"success": True, "provider": "telegram", "detail": "Message delivered."}

        # Attempt 2: If Telegram rejects Markdown entities (e.g. unescaped symbols in workouts),
        # retry as plain text to ensure delivery.
        if resp.status_code == 400 and "parse" in resp.text.lower():
            logger.warning("Telegram markdown parsing failed, retrying as plain text: %s", resp.text[:120])
            payload_plain = {
                "chat_id": target_chat_id,
                "text": text,
            }
            resp_plain = requests.post(url, json=payload_plain, timeout=10.0)
            if resp_plain.status_code == 200:
                return {
                    "success": True,
                    "provider": "telegram",
                    "detail": "Message delivered (plain text fallback).",
                }
            return {
                "success": False,
                "provider": "telegram",
                "detail": f"Telegram error (HTTP {resp_plain.status_code}): {resp_plain.text[:200]}",
            }

        return {
            "success": False,
            "provider": "telegram",
            "detail": f"Telegram error (HTTP {resp.status_code}): {resp.text[:200]}",
        }
    except Exception as e:
        return {"success": False, "provider": "telegram", "detail": f"Telegram request failed: {e}"}


def _strip_greek_accents(text: str) -> str:
    """Strip Greek monotonic accents for clean all-caps headers."""
    accents = str.maketrans("ΆΈΉΊΌΎΏάέήίόύώΐΰ", "ΑΕΗΙΟΥΩαεηιουωιυ")
    return text.translate(accents)


def format_next_day_brief(
    target_date: date,
    workout_text: str,
    weather_info: dict | None = None,
    coach_tip: str | None = None,
    lookup_error: str | None = None,
    recovery_info: dict | None = None,
    readiness_info: dict | None = None,
) -> str:
    """
    Format a clean, structured morning/evening briefing for Telegram.

    Includes weather-adjusted pacing guidance when outdoor running is detected
    and thermal/wind conditions warrant adjustment, plus Garmin recovery readiness.
    """
    weekday_raw = GREEK_DAY_NAMES.get(target_date.weekday(), "")
    weekday_gr = _strip_greek_accents(weekday_raw).upper()
    date_str = target_date.strftime("%d/%m/%Y")

    lines = [
        f"⚡ *ΠΡΟΠΟΝΗΣΗ ΗΜΕΡΑΣ — {weekday_gr} ({date_str})*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Workout section
    clean_workout = (workout_text or "").strip()
    if clean_workout:
        lines.append("🏋️‍♂️ *Πλάνο Προπονητή:*")
        lines.append(clean_workout)
    elif lookup_error:
        lines.append("⚠️ *Πλάνο:* Δεν ήταν δυνατή η ανάγνωση του προγράμματος.")
        lines.append(f"_({lookup_error})_")
        lines.append("Έλεγξε το Google Sheet πριν προπονηθείς.")
    else:
        lines.append("🏋️‍♂️ *Πλάνο:* Rest Day / Ελεύθερη ημέρα ή δεν έχει καταχωρηθεί ακόμη.")

    # Garmin Recovery & Readiness section (if available)
    if recovery_info and recovery_info.get("available"):
        sleep_h = recovery_info.get("sleep_hours")
        hrv_ms = recovery_info.get("hrv_last_night")
        rhr = recovery_info.get("resting_hr")
        bb = recovery_info.get("body_battery_latest") or recovery_info.get("body_battery_charged")
        status = recovery_info.get("recovery_status", "unknown")

        status_icon = "🟢" if status == "optimal" else ("🟡" if status == "adequate" else ("🟠" if status == "compromised" else "🔴"))
        lines.append(f"\n🔋 *Αποκατάσταση (Garmin) — {status_icon} {status.upper()}:*")
        items = []
        if sleep_h is not None:
            items.append(f"Ύπνος: *{sleep_h}h*")
        if hrv_ms is not None:
            items.append(f"HRV: *{hrv_ms}ms*")
        if rhr is not None:
            items.append(f"RHR: *{rhr} bpm*")
        if bb is not None:
            items.append(f"Battery: *{bb}%*")
        if items:
            lines.append("• " + " | ".join(items))

        if readiness_info and readiness_info.get("needs_modulation"):
            lines.append(f"• {readiness_info.get('modulation_advice')}")

    # Weather section
    if weather_info:
        icon = weather_info.get("icon", "🌤️")
        cond = weather_info.get("condition", "Fair")
        t_max = weather_info.get("temp_max_c")
        t_min = weather_info.get("temp_min_c")
        rain_mm = weather_info.get("precipitation_mm") or 0.0
        rain_pct = weather_info.get("precip_probability_pct") or 0
        wind = weather_info.get("wind_speed_max_kmh")

        temp_str = f"{round(t_min)}°C – {round(t_max)}°C" if t_max is not None and t_min is not None else "N/A"
        lines.append("\n🌤️ *Καιρός (Αθήνα):*")
        lines.append(f"• {icon} {cond} | {temp_str}")
        if rain_mm > 0 or rain_pct > 20:
            lines.append(f"• 💧 Βροχόπτωση: {rain_mm:.1f}mm ({rain_pct}%)")
        if wind:
            lines.append(f"• 💨 Άνεμος: {round(wind)} km/h")

        # Weather-adjusted pacing guidance for running
        is_run = any(w in _strip_greek_accents(clean_workout).lower() for w in ("τρεξιμο", "δρομικες", "run"))
        if is_run and t_max is not None:
            # Extract target pace if mentioned e.g. @ 5,15 or @ 5:15
            pace_match = re.search(r"@\s*(\d{1,2}[:,\.]\d{2})", clean_workout)
            base_pace_sec = parse_pace_string_to_sec(pace_match.group(1)) if pace_match else 315.0  # default 5:15
            adj = calculate_weather_pace_adjustment(
                base_pace_sec,
                temp_c=t_max,
                wind_kmh=wind,
            )
            if adj.get("needs_adjustment"):
                lines.append(f"\n🌡️ *Προσαρμογή Ρυθμού ({adj['heat_impact']}):*")
                if pace_match:
                    lines.append(f"• Στόχος: {pace_match.group(1)} → Προτεινόμενο: *{adj['adjusted_pace_range']}*")
                else:
                    lines.append(f"• Προσαρμογή: *+{adj['adjustment_sec']:.0f}s/χλμ* για αποφυγή καρδιακής επιβάρυνσης")
                if adj.get("advice"):
                    lines.append(f"• _{adj['advice']}_")

    # Coach Tip
    if coach_tip:
        lines.append(f"\n💡 *AI Coach Tip:* {coach_tip.strip()}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("🚀 Καλή προπόνηση!")

    return "\n".join(lines)


def handle_telegram_command(command_text: str, chat_id: str | None = None) -> str:
    """
    Process incoming commands from Telegram and return reply markdown.
    Validates sender against TELEGRAM_CHAT_ID.
    """
    from src.integrations.sheets import get_planned_workout_for_date
    from src.integrations.weather import get_weather_for_date
    from src.integrations.garmin import get_daily_recovery_metrics, assess_workout_readiness

    clean_text = (command_text or "").strip()
    if not clean_text:
        return "👋 Στείλε /help για να δεις τις διαθέσιμες εντολές."

    # Security check: only authorized athlete chat ID
    if TELEGRAM_CHAT_ID and chat_id and str(chat_id).strip() != TELEGRAM_CHAT_ID.strip():
        logger.warning("Unauthorized access attempt from Telegram chat_id %s", chat_id)
        return "⛔ Δεν έχεις δικαίωμα πρόσβασης σε αυτό το bot."

    lower = clean_text.lower()
    cmd = lower.split()[0] if lower else ""

    if cmd in ("/start", "/help", "help"):
        return (
            "🤖 *Endurance AI Telegram Assistant*\n\n"
            "Διαθέσιμες εντολές:\n"
            "• `/today` — Πλάνο προπόνησης, καιρός & αποκατάσταση για σήμερα\n"
            "• `/tomorrow` — Πλάνο προπόνησης & καιρός για αύριο\n"
            "• `/recovery` — Βιομετρικά Garmin (Ύπνος, HRV, Body Battery) & ετοιμότητα\n"
            "• `/compliance [YYYY-MM-DD]` — Αξιολόγηση εκτέλεσης προπόνησης vs πλάνο\n"
            "• `/sync` — Συγχρονισμός Strava & Garmin στο Google Sheets\n"
            "• `/stats` — Εβδομαδιαίος όγκος προπόνησης & δείκτης ACWR\n"
            "• `/gear` — Χιλιόμετρα παπουτσιών & ποδηλάτων\n"
            "• `/coach <ερώτηση>` — Συνομιλία με τον conversational AI Coach\n"
        )

    if cmd in ("/today", "today", "σημερα", "/simera"):
        target_d = date.today()
        w_info = get_planned_workout_for_date(target_d)
        weath = get_weather_for_date(target_d)
        rec = get_daily_recovery_metrics(target_d)
        readiness = assess_workout_readiness(rec, workout_text=w_info.get("workout_text", ""))
        tip = "Keep easy aerobic pace in Zone 2 for optimal recovery."
        return format_next_day_brief(
            target_date=target_d,
            workout_text=w_info.get("workout_text", ""),
            weather_info=weath,
            coach_tip=tip,
            lookup_error=w_info.get("reason"),
            recovery_info=rec,
            readiness_info=readiness,
        )

    if cmd in ("/tomorrow", "/next", "tomorrow", "αυριο", "/avrio"):
        target_d = date.today() + timedelta(days=1)
        w_info = get_planned_workout_for_date(target_d)
        weath = get_weather_for_date(target_d)
        rec = get_daily_recovery_metrics(target_d)
        readiness = assess_workout_readiness(rec, workout_text=w_info.get("workout_text", ""))
        tip = "Hydrate and fuel early for tomorrow's session."
        return format_next_day_brief(
            target_date=target_d,
            workout_text=w_info.get("workout_text", ""),
            weather_info=weath,
            coach_tip=tip,
            lookup_error=w_info.get("reason"),
            recovery_info=rec,
            readiness_info=readiness,
        )

    if cmd in ("/recovery", "recovery", "/health", "/sleep"):
        try:
            target_d = date.today()
            w_info = get_planned_workout_for_date(target_d)
            rec = get_daily_recovery_metrics(target_d)
            readiness = assess_workout_readiness(rec, workout_text=w_info.get("workout_text", ""))

            if not rec.get("available"):
                return "ℹ️ *Garmin Recovery:* Δεν βρέθηκαν διαθέσιμα βιομετρικά για σήμερα. Βεβαιώσου ότι το ρολόι έχει συγχρονιστεί με το Garmin Connect."

            status = rec.get("recovery_status", "unknown")
            icon = "🟢" if status == "optimal" else ("🟡" if status == "adequate" else ("🟠" if status == "compromised" else "🔴"))
            score = rec.get("recovery_score", 75)

            lines = [
                f"🔋 *Ημερήσια Αποκατάσταση & Ετοιμότητα*",
                "━━━━━━━━━━━━━━━━━━━━",
                f"• Κατάσταση: {icon} *{status.upper()}* (Score: {score}/100)",
            ]
            if rec.get("sleep_hours") is not None:
                lines.append(f"• 😴 Ύπνος: *{rec['sleep_hours']} ώρες*" + (f" (Score: {rec['sleep_score']})" if rec.get("sleep_score") else ""))
            if rec.get("hrv_last_night") is not None:
                lines.append(f"• 💓 HRV: *{rec['hrv_last_night']} ms* ({rec.get('hrv_status', 'unknown')})")
            if rec.get("resting_hr") is not None:
                lines.append(f"• 🫀 Resting HR: *{rec['resting_hr']} bpm*")
            bb = rec.get("body_battery_latest") or rec.get("body_battery_charged")
            if bb is not None:
                lines.append(f"• ⚡ Body Battery: *{bb}%*")
            if rec.get("avg_stress") is not None:
                lines.append(f"• 🧘 Stress Level: *{rec['avg_stress']}/100*")

            if rec.get("flags"):
                lines.append(f"• ⚠️ Σημάδια κόπωσης: _{', '.join(rec['flags'])}_")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"💡 *Οδηγία Προπόνησης:*\n{readiness.get('modulation_advice', '')}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Σφάλμα ανάκτησης αποκατάστασης: `{e}`"

    if cmd in ("/compliance", "compliance", "/execution"):
        try:
            parts = clean_text.split()
            target_str = parts[1] if len(parts) > 1 else date.today().isoformat()
            t_date = date.fromisoformat(target_str)
        except ValueError:
            return "⚠️ Μη έγκυρη ημερομηνία. Χρησιμοποίησε `/compliance YYYY-MM-DD` (π.χ. `/compliance 2026-09-08`)."

        try:
            from src.integrations.sheets import get_planned_workout_for_date
            from src.storage.activity_store import get_activities, init_db
            from src.analytics.compliance import evaluate_daily_compliance

            w_info = get_planned_workout_for_date(t_date)
            planned_text = w_info.get("workout_text", "")

            conn = init_db()
            acts = get_activities(conn, limit=250)
            conn.close()

            day_acts = [a for a in acts if (a.get("start_date_local") or "").startswith(t_date.isoformat())]
            comp = evaluate_daily_compliance(planned_text, day_acts)
            comp["planned_text"] = planned_text
            score = comp.get("compliance_score", 100)
            score_icon = "🟢" if score >= 80 else ("🟡" if score >= 50 else "🔴")

            lines = [
                f"🎯 *Συμμόρφωση Προπόνησης — {t_date.strftime('%d/%m/%Y')}*",
                "━━━━━━━━━━━━━━━━━━━━",
                f"• Βαθμός Εκτέλεσης: {score_icon} *{score}%*",
                f"• Πλάνο Προπονητή:\n  _{comp.get('planned_text') or 'Δεν βρέθηκε καταχωρημένο πλάνο'}_",
            ]
            matches = comp.get("matches", [])
            if matches:
                lines.append("• Εκτελεσθείσες Δραστηριότητες:")
                for m in matches:
                    st = m.get("status", "unknown").upper()
                    st_icon = "✅" if "COMPLIANT" in st else ("🟡" if "PARTIAL" in st else "ℹ️")
                    lines.append(f"  {st_icon} *{m.get('sport')}*: {m.get('actual_distance_km', 0):.1f}km @ {m.get('actual_pace', 'N/A')} ({st})")
            else:
                lines.append("• Καμία δραστηριότητα Strava δεν ταυτοποιήθηκε για αυτή την ημέρα.")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"📝 {comp.get('summary', '')}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Σφάλμα αξιολόγησης συμμόρφωσης: `{e}`"

    if cmd in ("/sync", "sync", "/sheets"):
        try:
            from src.integrations.strava import fetch_activities, fetch_details_for_activities, get_access_token
            from src.integrations.sheets import write_to_sheet

            token = get_access_token(interactive=False)
            summaries = fetch_activities(token, per_page=30)
            details = fetch_details_for_activities(token, summaries, delay_sec=0.5)
            write_to_sheet(summaries, details)
            return f"✅ *Συγχρονισμός ολοκληρώθηκε!*\nΕνημερώθηκαν τα κελιά στο Google Sheets για τις τελευταίες {len(summaries)} δραστηριότητες."
        except Exception as e:
            return f"❌ *Σφάλμα συγχρονισμού:* `{e}`"

    if cmd in ("/stats", "stats", "/volume"):
        try:
            import os
            import json
            from src.config import ACTIVITIES_CACHE_FILE
            from src.storage.activity_store import get_activities, init_db
            from src.analytics.metrics import calculate_acwr, process_activities_into_weeks

            conn = init_db()
            acts = get_activities(conn, limit=200)
            conn.close()

            if not acts and os.path.exists(ACTIVITIES_CACHE_FILE):
                try:
                    with open(ACTIVITIES_CACHE_FILE) as f:
                        acts = json.load(f).get("activities", [])
                except Exception:
                    acts = []

            if not acts:
                return "ℹ️ Δεν βρέθηκαν καταγεγραμμένες δραστηριότητες."

            weeks = process_activities_into_weeks(acts)
            if not weeks:
                return "ℹ️ Δεν έχουν υπολογιστεί εβδομαδιαία στατιστικά."

            sorted_keys = sorted(weeks.keys())
            curr_key = sorted_keys[-1]
            current_week = weeks[curr_key]
            acwr_map = calculate_acwr(sorted_keys, weeks)
            curr_acwr = acwr_map.get(curr_key, {})

            run_km = current_week.get("run_dist_km", 0.0)
            bike_km = current_week.get("bike_dist_km", 0.0)
            swim_m = current_week.get("swim_dist_m", 0)
            total_h = current_week.get("total_time_sec", 0) / 3600.0
            count = len(current_week.get("activities", []))

            ratio_val = curr_acwr.get("acwr_ratio")
            ratio_str = f"{ratio_val:.2f}" if ratio_val is not None else "N/A"
            zone_str = str(curr_acwr.get("zone", "unknown")).capitalize()

            return (
                f"📊 *Εβδομάδα {curr_key} — Στατιστικά*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"• Προπονήσεις: *{count}*\n"
                f"• Συνολικός Χρόνος: *{total_h:.1f} ώρες*\n"
                f"• 🏃 Τρέξιμο: *{run_km:.1f} km*\n"
                f"• 🚴 Ποδηλασία: *{bike_km:.1f} km*\n"
                f"• 🏊 Κολύμβηση: *{swim_m:.0f} m*\n"
                f"• ⚡ ACWR Ratio: *{ratio_str}* ({zone_str})\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        except Exception as e:
            return f"❌ Σφάλμα στατιστικών: `{e}`"

    if cmd in ("/gear", "/shoes", "shoes", "gear"):
        try:
            import os
            import json
            from src.config import ACTIVITIES_CACHE_FILE
            from src.storage.activity_store import get_activities, get_details, init_db
            from src.analytics.gear import extract_gear_summary

            conn = init_db()
            acts = get_activities(conn, limit=250)
            details = get_details(conn)
            conn.close()

            if not acts and os.path.exists(ACTIVITIES_CACHE_FILE):
                try:
                    with open(ACTIVITIES_CACHE_FILE) as f:
                        cached = json.load(f)
                        acts = cached.get("activities", [])
                        details = cached.get("details", {})
                except Exception:
                    acts, details = [], {}

            summary = extract_gear_summary(acts, details=details)
            if not summary:
                return "👟 Δεν βρέθηκαν καταγεγραμμένα παπούτσια ή ποδήλατα με gear_id."

            lines = ["👟 *Χιλιόμετρα Εξοπλισμού & Παπουτσιών*\n━━━━━━━━━━━━━━━━━━━━"]
            for g in summary:
                alert_icon = "⚠️" if g["alert"] else ("🟡" if g["status"] == "warning" else "🟢")
                lines.append(
                    f"{alert_icon} *{g['name']}* ({g['gear_type']}):\n"
                    f"   • Χιλιόμετρα: *{g['total_km']:.1f} km* / Όριο: {g['threshold_km']:.0f} km\n"
                    f"   • Υπολειπόμενα: *{g['remaining_km']:.1f} km* ({g['activity_count']} προπονήσεις)"
                )
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Σφάλμα εξοπλισμού: `{e}`"

    # Conversational AI Coach
    prompt = clean_text
    if cmd in ("/coach", "coach"):
        prompt = clean_text[len(cmd):].strip()

    if not prompt:
        return "🤖 Στείλε `/coach <η ερώτησή σου>` για να μιλήσεις με τον AI Coach!"

    try:
        from src.analytics.coach_agent import chat as coach_chat

        res = coach_chat(
            message=prompt,
            session_id="telegram_athlete_chat",
        )
        return res.get("reply", "Δεν έλαβα απάντηση από τον AI Coach.")
    except Exception as e:
        return f"⚠️ Ο AI Coach δεν είναι διαθέσιμος αυτή τη στιγμή: `{e}`"


def poll_telegram_updates(offset: int = 0, timeout: int = 20) -> tuple[int, list[dict]]:
    """Fetch updates from Telegram Bot API with long-polling."""
    token = (TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        return offset, []

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": timeout}

    try:
        resp = requests.get(url, params=params, timeout=timeout + 5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                updates = data.get("result", [])
                next_offset = offset
                for u in updates:
                    next_offset = max(next_offset, u.get("update_id", 0) + 1)
                return next_offset, updates
    except Exception as e:
        logger.debug("Telegram polling transient error: %s", e)

    return offset, []


def process_incoming_update(update: dict) -> None:
    """Process a single Telegram update message."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = message.get("text", "")

    if not text:
        return

    reply = handle_telegram_command(text, chat_id=chat_id)
    if reply:
        send_telegram_message(reply, chat_id=chat_id)


def run_daily_dispatch_check() -> bool:
    """
    Check if the current Athens time matches TELEGRAM_DAILY_DISPATCH_TIME,
    and if so, automatically send tomorrow's workout briefing once per day.
    """
    global _LAST_DISPATCHED_DATE

    now_dt = datetime.now()
    today_str = now_dt.strftime("%Y-%m-%d")
    current_hhmm = now_dt.strftime("%H:%M")

    target_time = (TELEGRAM_DAILY_DISPATCH_TIME or "20:30").strip()
    if current_hhmm == target_time and _LAST_DISPATCHED_DATE != today_str:
        logger.info("Executing daily automated Telegram dispatch for tomorrow's workout...")
        from src.integrations.sheets import get_planned_workout_for_date
        from src.integrations.weather import get_weather_for_date
        from src.integrations.garmin import get_daily_recovery_metrics, assess_workout_readiness

        target_date = date.today() + timedelta(days=1)
        w_info = get_planned_workout_for_date(target_date)
        weath = get_weather_for_date(target_date)
        rec = get_daily_recovery_metrics(target_date)
        readiness = assess_workout_readiness(rec, workout_text=w_info.get("workout_text", ""))
        tip = "Keep easy aerobic pace in Zone 2 for optimal recovery and mitochondrial adaptation."

        brief = format_next_day_brief(
            target_date=target_date,
            workout_text=w_info.get("workout_text", ""),
            weather_info=weath,
            coach_tip=tip,
            lookup_error=w_info.get("reason"),
            recovery_info=rec,
            readiness_info=readiness,
        )
        res = send_telegram_message(brief)
        if res.get("success"):
            _LAST_DISPATCHED_DATE = today_str
            logger.info("Daily Telegram dispatch sent successfully for %s", target_date)
            return True
        else:
            logger.warning("Daily Telegram dispatch delivery failed: %s", res.get("detail"))

    return False
