"""
Telegram Notification Dispatcher.

Sends training alerts, daily briefs, and next-day workouts to athlete's Telegram
via Telegram Bot API, with console fallback for dry-run/testing.
"""

import logging
from datetime import date
import requests

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

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
) -> str:
    """
    Format a clean, structured morning/evening briefing for Telegram.

    `lookup_error` distinguishes "the sheet says nothing is planned" from "the
    plan could not be read". Both arrive here as an empty `workout_text`, and
    reporting the second as a rest day tells the athlete not to train on a day
    the coach may well have programmed.
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

    # Coach Tip
    if coach_tip:
        lines.append(f"\n💡 *AI Coach Tip:* {coach_tip.strip()}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("🚀 Καλή προπόνηση!")

    return "\n".join(lines)
