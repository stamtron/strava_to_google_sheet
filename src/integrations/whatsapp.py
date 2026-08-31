"""
WhatsApp Notification Dispatcher.

Sends training alerts, daily briefs, and next-day workouts to athlete's WhatsApp
via CallMeBot (free personal WhatsApp gateway) or Twilio WhatsApp API, with console
fallback for dry-run/testing.
"""

import logging
import urllib.parse
from datetime import date, datetime
import requests

from src.config import (
    CALLMEBOT_API_KEY,
    CALLMEBOT_PHONE,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM,
    TWILIO_WHATSAPP_TO,
    WHATSAPP_PROVIDER,
)

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


def send_whatsapp_message(message: str) -> dict:
    """
    Dispatch a message to athlete's WhatsApp using the configured provider.

    Returns {'success': bool, 'provider': str, 'detail': str}.
    """
    provider = (WHATSAPP_PROVIDER or "console").strip().lower()
    text = (message or "").strip()
    if not text:
        return {"success": False, "provider": provider, "detail": "Empty message."}

    # 1. CallMeBot (Free personal WhatsApp API)
    if provider == "callmebot":
        if not CALLMEBOT_PHONE or not CALLMEBOT_API_KEY:
            logger.info("CallMeBot phone/apikey not configured; printing to console.")
            print(f"\n📱 [WhatsApp / CallMeBot (Dry Run)]\n{text}\n")
            return {
                "success": True,
                "provider": "callmebot (dry-run)",
                "detail": "CALLMEBOT_PHONE or CALLMEBOT_API_KEY unset; printed to console.",
            }

        phone = CALLMEBOT_PHONE.replace(" ", "").replace("-", "")
        if not phone.startswith("+") and not phone.startswith("00"):
            phone = "+" + phone

        encoded_text = urllib.parse.quote_plus(text)
        url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded_text}&apikey={CALLMEBOT_API_KEY}"

        try:
            resp = requests.get(url, timeout=10.0)
            if resp.status_code == 200 and "error" not in resp.text.lower():
                return {"success": True, "provider": "callmebot", "detail": "Message delivered."}
            return {
                "success": False,
                "provider": "callmebot",
                "detail": f"CallMeBot error (HTTP {resp.status_code}): {resp.text[:200]}",
            }
        except Exception as e:
            return {"success": False, "provider": "callmebot", "detail": f"Request failed: {e}"}

    # 2. Twilio WhatsApp API
    elif provider == "twilio":
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            print(f"\n📱 [WhatsApp / Twilio (Dry Run)]\n{text}\n")
            return {
                "success": True,
                "provider": "twilio (dry-run)",
                "detail": "TWILIO credentials unset; printed to console.",
            }

        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        data = {
            "From": TWILIO_WHATSAPP_FROM or "whatsapp:+14155238886",
            "To": TWILIO_WHATSAPP_TO or CALLMEBOT_PHONE,
            "Body": text,
        }
        try:
            resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=10.0)
            if resp.status_code in (200, 201):
                return {"success": True, "provider": "twilio", "detail": "Message dispatched via Twilio."}
            return {"success": False, "provider": "twilio", "detail": f"Twilio error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "provider": "twilio", "detail": f"Twilio request failed: {e}"}

    # 3. Console / Dry-run Fallback
    print(f"\n📱 [WhatsApp (Console Dispatch)]\n{text}\n")
    return {"success": True, "provider": "console", "detail": "Message printed to console."}


def _strip_greek_accents(text: str) -> str:
    """Strip Greek monotonic accents for clean all-caps headers."""
    accents = str.maketrans("ΆΈΉΊΌΎΏάέήίόύώΐΰ", "ΑΕΗΙΟΥΩαεηιουωιυ")
    return text.translate(accents)


def format_next_day_brief(
    target_date: date,
    workout_text: str,
    weather_info: dict | None = None,
    coach_tip: str | None = None,
) -> str:
    """
    Format a clean, structured morning/evening briefing for WhatsApp.
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
    else:
        lines.append("🏋️‍♂️ *Πλάνο:* Rest Day / Ελεύθερη ημέρα ή δεν έχει καταχωρηθεί ακόμη.")

    # Weather section
    if weather_info:
        icon = weather_info.get("icon", "🌤️")
        cond = weather_info.get("condition", "Fair")
        t_max = weather_info.get("temp_max_c")
        t_min = weather_info.get("temp_min_c")
        rain_mm = weather_info.get("precipitation_mm", 0.0)
        rain_pct = weather_info.get("precip_probability_pct", 0)
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
