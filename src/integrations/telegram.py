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

from src.analytics.weather_pace import calculate_weather_pace_adjustment, parse_pace_string_to_sec
from src.config import (
    DEFAULT_RUN_PACE_SEC_PER_KM,
    STRAVA_DETAIL_DELAY_SEC,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_DAILY_DISPATCH_FALLBACK,
    TELEGRAM_DISPATCH_RETRY_BACKOFF_SEC,
    TELEGRAM_HTTP_TIMEOUT_SEC,
    TELEGRAM_MAX_MESSAGE_CHARS,
    TELEGRAM_WEEKLY_DISPATCH_FALLBACK,
)
from src.formatting import format_mmss
from src.status_labels import (
    HEAT_IMPACT_LABELS,
    acwr_label,
    compliance_summary,
    gear_display_name,
    readiness_advice,
    tsb_label,
    weather_advice,
    weather_pace_range,
)
from src.timeutil import now_local, today_local

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

# Epoch seconds of the last failed dispatch attempt per kind ("daily"/"weekly"),
# so a failing send backs off instead of retrying on every bot-loop iteration.
_LAST_DISPATCH_FAILURE: dict[str, float] = {}

_BOT_URL_TOKEN_RE = re.compile(r"/bot[^/\s]+/")


def redact_token(value: Any) -> str:
    """
    Strip the bot token from any string (exception text, URLs, response bodies).

    requests embeds the full https://api.telegram.org/bot<TOKEN>/... URL in its
    exception messages, so anything logged or returned must pass through here.
    """
    text = str(value)
    token = (TELEGRAM_BOT_TOKEN or "").strip()
    if token:
        text = text.replace(token, "<redacted>")
    return _BOT_URL_TOKEN_RE.sub("/bot<redacted>/", text)


def _hard_split(line: str, limit: int) -> list[str]:
    """Split a single overlong line into <= limit pieces, never inside an <html> tag."""
    pieces = []
    while len(line) > limit:
        cut = limit
        open_idx = line.rfind("<", 0, cut)
        if open_idx > line.rfind(">", 0, cut) and open_idx > 0:
            cut = open_idx
        pieces.append(line[:cut])
        line = line[cut:]
    if line:
        pieces.append(line)
    return pieces


def split_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> list[str]:
    """
    Split text into chunks of at most `limit` chars for Telegram's 4096-char cap.

    Breaks at newlines where possible so formatting survives; only a single line
    longer than the limit is hard-split.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        for piece in _hard_split(line, limit) if len(line) > limit else [line]:
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= limit:
                current += "\n" + piece
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _send_chunk(url: str, chat_id: str, text: str) -> dict:
    """Send one <= limit chunk, falling back to plain text if Markdown is rejected."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    resp = requests.post(url, json=payload, timeout=TELEGRAM_HTTP_TIMEOUT_SEC)
    if resp.status_code == 200:
        return {"success": True, "provider": "telegram", "detail": "Message delivered."}

    # Attempt 2: If Telegram rejects Markdown entities (e.g. unescaped symbols in workouts),
    # retry as plain text to ensure delivery.
    if resp.status_code == 400 and "parse" in resp.text.lower():
        logger.warning(
            "Telegram markdown parsing failed, retrying as plain text: %s", redact_token(resp.text[:120])
        )
        payload_plain = {
            "chat_id": chat_id,
            "text": text,
        }
        resp_plain = requests.post(url, json=payload_plain, timeout=TELEGRAM_HTTP_TIMEOUT_SEC)
        if resp_plain.status_code == 200:
            return {
                "success": True,
                "provider": "telegram",
                "detail": "Message delivered (plain text fallback).",
            }
        return {
            "success": False,
            "provider": "telegram",
            "detail": f"Telegram error (HTTP {resp_plain.status_code}): {redact_token(resp_plain.text[:200])}",
        }

    return {
        "success": False,
        "provider": "telegram",
        "detail": f"Telegram error (HTTP {resp.status_code}): {redact_token(resp.text[:200])}",
    }


def send_telegram_message(message: str, chat_id: str | None = None) -> dict:
    """
    Dispatch a message to athlete's Telegram via Telegram Bot API.

    Messages over TELEGRAM_MAX_MESSAGE_CHARS are split at newlines and sent in
    order. Falls back to plain text if Markdown entity parsing fails, and falls
    back to console print if credentials are not configured.

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
    chunks = split_message(text)

    result: dict = {}
    for idx, chunk in enumerate(chunks, start=1):
        try:
            result = _send_chunk(url, target_chat_id, chunk)
        except Exception as e:
            result = {
                "success": False,
                "provider": "telegram",
                "detail": f"Telegram request failed: {redact_token(e)}",
            }
        if not result["success"]:
            if len(chunks) > 1:
                result["detail"] = f"Part {idx}/{len(chunks)}: {result['detail']}"
            return result

    if len(chunks) > 1:
        result["detail"] = f"Message delivered in {len(chunks)} parts."
    return result


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
            lines.append(f"• {readiness_advice(readiness_info)}")

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
            base_pace_sec = parse_pace_string_to_sec(pace_match.group(1)) if pace_match else DEFAULT_RUN_PACE_SEC_PER_KM
            adj = calculate_weather_pace_adjustment(
                base_pace_sec,
                temp_c=t_max,
                wind_kmh=wind,
            )
            if adj.get("needs_adjustment"):
                lines.append(f"\n🌡️ *Προσαρμογή Ρυθμού ({HEAT_IMPACT_LABELS[adj['heat_impact']]}):*")
                if pace_match:
                    lines.append(f"• Στόχος: {pace_match.group(1)} → Προτεινόμενο: *{weather_pace_range(adj)}*")
                else:
                    lines.append(f"• Προσαρμογή: *+{adj['adjustment_sec']:.0f}s/χλμ* για αποφυγή καρδιακής επιβάρυνσης")
                advice = weather_advice(adj)
                if advice:
                    lines.append(f"• _{advice}_")

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
    from src.config import TELEGRAM_CHAT_ID
    from src.integrations.garmin import assess_workout_readiness, get_daily_recovery_metrics
    from src.integrations.sheets import get_planned_workout_for_date
    from src.integrations.weather import get_weather_for_date

    clean_text = (command_text or "").strip()
    if not clean_text:
        return "👋 Στείλε /help για να δεις τις διαθέσιμες εντολές."

    configured_chat_id = (TELEGRAM_CHAT_ID or "").strip()
    if not configured_chat_id:
        logger.warning("Unauthorized access attempt: TELEGRAM_CHAT_ID is unset.")
        return "⛔ Δεν έχεις δικαίωμα πρόσβασης σε αυτό το bot."

    # When chat_id is provided, verify it strictly matches configured_chat_id
    if chat_id is not None and (not str(chat_id).strip() or str(chat_id).strip() != configured_chat_id):
        logger.warning("Unauthorized access attempt from Telegram chat_id %s", chat_id)
        return "⛔ Δεν έχεις δικαίωμα πρόσβασης σε αυτό το bot."

    lower = clean_text.lower()
    cmd = lower.split()[0] if lower else ""

    if cmd in ("/start", "/help", "help"):
        return (
            "🤖 *StaminAI Telegram Assistant*\n\n"
            "Διαθέσιμες εντολές:\n"
            "• `/today` — Πλάνο προπόνησης, καιρός & αποκατάσταση για σήμερα\n"
            "• `/tomorrow` — Πλάνο προπόνησης & καιρός για αύριο\n"
            "• `/recovery` — Βιομετρικά Garmin (Ύπνος, HRV, Body Battery) & ετοιμότητα\n"
            "• `/form` (ή `/tsb`) — Κατάσταση Φόρμας (CTL Fitness, ATL Fatigue, TSB Form)\n"
            "• `/weekly` — Εβδομαδιαία ανασκόπηση όγκου, ACWR, φόρμας & εξοπλισμού\n"
            "• `/compliance [YYYY-MM-DD]` — Αξιολόγηση εκτέλεσης προπόνησης vs πλάνο\n"
            "• `/sync` — Συγχρονισμός Strava & Garmin στο Google Sheets\n"
            "• `/stats` — Εβδομαδιαίος όγκος προπόνησης & δείκτης ACWR\n"
            "• `/gear` — Χιλιόμετρα παπουτσιών & ποδηλάτων\n"
            "• `/coach <ερώτηση>` — Συνομιλία με τον conversational AI Coach\n"
        )

    if cmd in ("/today", "today", "σημερα", "/simera"):
        target_d = today_local()
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
        target_d = today_local() + timedelta(days=1)
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
            target_d = today_local()
            w_info = get_planned_workout_for_date(target_d)
            rec = get_daily_recovery_metrics(target_d)
            readiness = assess_workout_readiness(rec, workout_text=w_info.get("workout_text", ""))

            if not rec.get("available"):
                return "ℹ️ *Garmin Recovery:* Δεν βρέθηκαν διαθέσιμα βιομετρικά για σήμερα. Βεβαιώσου ότι το ρολόι έχει συγχρονιστεί με το Garmin Connect."

            status = rec.get("recovery_status", "unknown")
            icon = "🟢" if status == "optimal" else ("🟡" if status == "adequate" else ("🟠" if status == "compromised" else "🔴"))
            score = rec.get("recovery_score", 75)

            lines = [
                "🔋 *Ημερήσια Αποκατάσταση & Ετοιμότητα*",
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
            lines.append(f"💡 *Οδηγία Προπόνησης:*\n{readiness_advice(readiness)}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Σφάλμα ανάκτησης αποκατάστασης: `{e}`"

    if cmd in ("/form", "/tsb", "form", "tsb", "/fitness", "fitness"):
        try:
            import json
            import os

            from src.analytics.metrics import calculate_fitness_fatigue
            from src.config import ACTIVITIES_CACHE_FILE
            from src.storage.activity_store import get_activities, init_db

            conn = init_db()
            acts = get_activities(conn, limit=250)
            conn.close()

            if not acts and os.path.exists(ACTIVITIES_CACHE_FILE):
                try:
                    with open(ACTIVITIES_CACHE_FILE) as f:
                        acts = json.load(f).get("activities", [])
                except Exception:
                    acts = []

            if not acts:
                return "ℹ️ Δεν βρέθηκαν δραστηριότητες για τον υπολογισμό Fitness/Fatigue/Form."

            perf = calculate_fitness_fatigue(acts)
            cur = perf.get("current", {})

            ctl = cur.get("ctl", 0.0)
            atl = cur.get("atl", 0.0)
            tsb = cur.get("tsb", 0.0)
            status = cur.get("status", "neutral")
            label = tsb_label(cur.get("status"))
            ramp = cur.get("ctl_ramp_7d", 0.0)

            # Visual indicator for TSB
            if tsb >= 10.0:
                tsb_icon = "🟢"
            elif tsb >= -10.0:
                tsb_icon = "🔵"
            elif tsb >= -30.0:
                tsb_icon = "🟡"
            else:
                tsb_icon = "🔴"

            ramp_str = f"+{ramp:.1f}" if ramp > 0 else f"{ramp:.1f}"

            lines = [
                "📈 *Κατάσταση Φόρμας & Προπονητικής Επιβάρυνσης (Banister EWMA)*",
                "━━━━━━━━━━━━━━━━━━━━",
                f"• 🏋️‍♂️ *Fitness (CTL 42d):* `{ctl:.1f}` ({ramp_str} τις τελευταίες 7ημ)",
                f"• ⚡ *Fatigue (ATL 7d):* `{atl:.1f}`",
                f"• {tsb_icon} *Form (TSB = CTL - ATL):* `{tsb:+.1f}`",
                f"• 🏷️ *Κατάσταση:* *{label}*",
                "━━━━━━━━━━━━━━━━━━━━",
            ]

            if status == "fresh":
                lines.append("💡 *Ερμηνεία:* Εξαιρετική φρεσκάδα και αγωνιστική ετοιμότητα. Ιδανικό παράθυρο για αγώνα, τεστ ή γρήγορο tempo!")
            elif status == "optimal":
                lines.append("💡 *Ερμηνεία:* Βέλτιστη προπονητική υπερφόρτωση (Optimal Overload). Χτίζεις αερόβια αντοχή χωρίς υπερβολική κόπωση.")
            elif status == "high_fatigue":
                lines.append("⚠️ *Ερμηνεία:* Υψηλή συσσωρευμένη κόπωση (High Fatigue). Απαραίτητη η αποφόρτιση ή χαλαρή αερόβια Z1/Z2 για αποφυγή υπερκόπωσης.")
            elif status == "transition":
                lines.append("💡 *Ερμηνεία:* Πολύ υψηλή φρεσκάδα / απώλεια φυσικής κατάστασης. Ώρα για σταδιακή επαναφορά του όγκου.")
            else:
                lines.append("💡 *Ερμηνεία:* Ισορροπημένη κατάσταση συντήρησης.")

            return "\n".join(lines)
        except Exception as e:
            return f"❌ Σφάλμα υπολογισμού φόρμας: `{e}`"

    if cmd in ("/weekly", "weekly", "/week", "week"):
        try:
            summary_text, _ = build_weekly_summary()
            return summary_text
        except Exception as e:
            return f"❌ Σφάλμα δημιουργίας εβδομαδιαίας ανασκόπησης: `{e}`"

    if cmd in ("/compliance", "compliance", "/execution"):
        try:
            parts = clean_text.split()
            target_str = parts[1] if len(parts) > 1 else today_local().isoformat()
            t_date = date.fromisoformat(target_str)
        except ValueError:
            return "⚠️ Μη έγκυρη ημερομηνία. Χρησιμοποίησε `/compliance YYYY-MM-DD` (π.χ. `/compliance 2026-09-08`)."

        try:
            from src.analytics.compliance import evaluate_daily_compliance
            from src.integrations.sheets import get_planned_workout_for_date
            from src.storage.activity_store import get_activities, init_db

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
                    st = m.get("status", "unknown")
                    st_icon = {"spot_on": "✅", "overreach": "🟡", "underreach": "🟡", "missed": "❌"}.get(st, "ℹ️")
                    pace_sec = m.get("actual_pace_sec")
                    unit = "/100μ" if m.get("pace_unit") == "per_100m" else "/χλμ"
                    pace = f"{format_mmss(pace_sec)}{unit}" if pace_sec else "N/A"
                    lines.append(f"  {st_icon} *{m.get('sport')}*: {m.get('actual_km') or 0:.1f}km @ {pace} ({st})")
            else:
                lines.append("• Καμία δραστηριότητα Strava δεν ταυτοποιήθηκε για αυτή την ημέρα.")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"📝 {compliance_summary(comp)}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Σφάλμα αξιολόγησης συμμόρφωσης: `{e}`"

    if cmd in ("/sync", "sync", "/sheets"):
        try:
            from src.integrations.sheets import write_to_sheet
            from src.integrations.strava import fetch_activities, fetch_details_for_activities, get_access_token
            from src.storage.activity_store import get_details, init_db, upsert_activities, upsert_details

            token = get_access_token(interactive=False)
            summaries = fetch_activities(token, per_page=30)
            conn = init_db()
            try:
                # Known details are reused so re-syncing the same window costs no
                # extra Strava calls (the main defence against the rate limit).
                details = fetch_details_for_activities(
                    token, summaries, known_details=get_details(conn), delay_sec=STRAVA_DETAIL_DELAY_SEC
                )
                upsert_activities(conn, summaries)
                if details:
                    upsert_details(conn, details)
            finally:
                conn.close()
            write_to_sheet(summaries, details)
            return f"✅ *Συγχρονισμός ολοκληρώθηκε!*\nΕνημερώθηκαν τα κελιά στο Google Sheets για τις τελευταίες {len(summaries)} δραστηριότητες."
        except Exception as e:
            return f"❌ *Σφάλμα συγχρονισμού:* `{redact_token(e)}`"

    if cmd in ("/stats", "stats", "/volume"):
        try:
            import json
            import os

            from src.analytics.metrics import calculate_acwr, process_activities_into_weeks
            from src.config import ACTIVITIES_CACHE_FILE
            from src.storage.activity_store import get_activities, init_db

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
            import json
            import os

            from src.analytics.gear import extract_gear_summary
            from src.config import ACTIVITIES_CACHE_FILE
            from src.storage.activity_store import get_activities, get_details, init_db

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
                    f"{alert_icon} *{gear_display_name(g)}* ({g['gear_type']}):\n"
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


def get_telegram_webhook_info() -> dict | None:
    """Query Telegram Bot API for current webhook status."""
    token = (TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    try:
        resp = requests.get(url, timeout=TELEGRAM_HTTP_TIMEOUT_SEC)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
    except Exception as e:
        logger.warning("Could not check Telegram webhook info: %s", redact_token(e))
    return None


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
        elif resp.status_code == 409:
            logger.warning(
                "Telegram polling conflict (HTTP 409): a webhook is active or another bot instance is polling."
            )
        else:
            logger.warning(
                "Telegram polling returned HTTP %d: %s", resp.status_code, redact_token(resp.text[:120])
            )
    except Exception as e:
        logger.warning("Telegram polling transient error: %s", redact_token(e))

    return offset, []


def process_incoming_update(update: dict) -> None:
    """
    Process a single Telegram update message.

    Edits are ignored (re-running a command because a typo was fixed would
    re-trigger syncs), and updates from any chat other than TELEGRAM_CHAT_ID are
    dropped silently so the bot never reveals itself to strangers.
    """
    message = update.get("message")
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", "")).strip()
    text = message.get("text", "")

    if not text:
        return

    configured_chat_id = (TELEGRAM_CHAT_ID or "").strip()
    if not configured_chat_id or chat_id != configured_chat_id:
        logger.warning("Ignoring Telegram update from unauthorized chat_id %s", chat_id or "<none>")
        return

    reply = handle_telegram_command(text, chat_id=chat_id)
    if reply:
        send_telegram_message(reply, chat_id=chat_id)


def build_daily_brief(
    target_date: date,
    custom_tip: str | None = None,
) -> tuple[str, dict]:
    """
    Assemble tomorrow's or today's planned workout brief, weather forecast,
    Garmin recovery biometrics, and coaching tip into formatted Markdown.

    Returns (brief_text, metadata_dict).
    """
    from src.config import (
        BRIEF_HEAT_THRESHOLD_C,
        BRIEF_RAIN_THRESHOLD_MM,
        DEFAULT_COACH_TIP,
        DEFAULT_HEAT_TIP,
        DEFAULT_RAIN_TIP,
    )
    from src.integrations.garmin import assess_workout_readiness, get_daily_recovery_metrics
    from src.integrations.sheets import get_planned_workout_for_date
    from src.integrations.weather import get_weather_for_date

    workout_info = get_planned_workout_for_date(target_date)
    weather_info = get_weather_for_date(target_date)
    recovery_info = get_daily_recovery_metrics(target_date)
    readiness_info = assess_workout_readiness(recovery_info, workout_text=workout_info.get("workout_text", ""))

    tip = custom_tip
    if not tip:
        precip = (weather_info.get("precipitation_mm") or 0.0) if weather_info else 0.0
        t_max = (weather_info.get("temp_max_c") or 0.0) if weather_info else 0.0
        if precip > BRIEF_RAIN_THRESHOLD_MM:
            tip = DEFAULT_RAIN_TIP
        elif t_max > BRIEF_HEAT_THRESHOLD_C:
            tip = DEFAULT_HEAT_TIP
        else:
            tip = DEFAULT_COACH_TIP

    brief_text = format_next_day_brief(
        target_date=target_date,
        workout_text=workout_info.get("workout_text", ""),
        weather_info=weather_info,
        coach_tip=tip,
        lookup_error=workout_info.get("reason"),
        recovery_info=recovery_info,
        readiness_info=readiness_info,
    )

    meta = {
        "workout_info": workout_info,
        "weather_info": weather_info,
        "recovery_info": recovery_info,
        "readiness_info": readiness_info,
        "coach_tip": tip,
    }
    return brief_text, meta


def build_weekly_summary(week_key: str | None = None) -> tuple[str, dict]:
    """
    Generate an executive weekly training summary including:
    - Total volume by sport (run, bike, swim, strength)
    - ACWR and load status
    - Banister Fitness (CTL), Fatigue (ATL), and Form (TSB)
    - Weekly Garmin biometrics (sleep, HRV, RHR) and baseline comparison
    - Gear alert warnings (shoes approaching retirement)
    """
    import json
    import os

    from src.analytics.gear import extract_gear_summary
    from src.analytics.metrics import (
        calculate_acwr,
        calculate_fitness_fatigue,
        process_activities_into_weeks,
    )
    from src.config import ACTIVITIES_CACHE_FILE
    from src.integrations.garmin import _load_garmin_cache, compute_biometric_baselines
    from src.storage.activity_store import get_activities, get_details, init_db

    conn = init_db()
    acts = get_activities(conn, limit=250)
    details = get_details(conn)
    conn.close()

    if not acts and os.path.exists(ACTIVITIES_CACHE_FILE):
        try:
            with open(ACTIVITIES_CACHE_FILE) as f:
                c = json.load(f)
                acts = c.get("activities", [])
                details = c.get("details", {})
        except Exception:
            pass

    weeks = process_activities_into_weeks(acts)
    sorted_keys = sorted(weeks.keys())
    if not sorted_keys:
        return "ℹ️ Δεν βρέθηκαν δραστηριότητες για τη σύνταξη της εβδομαδιαίας αναφοράς.", {}

    target_key = week_key if week_key and week_key in weeks else sorted_keys[-1]
    week = weeks[target_key]
    acwr_map = calculate_acwr(sorted_keys, weeks)
    acwr = acwr_map.get(target_key, {})

    perf = calculate_fitness_fatigue(acts)
    cur_perf = perf.get("current", {})

    garmin_cache = _load_garmin_cache()
    garmin_entry = garmin_cache.get(target_key, {})
    garmin_week = garmin_entry.get("summary") if isinstance(garmin_entry, dict) else None
    baselines = compute_biometric_baselines()

    gear_list = extract_gear_summary(acts, details=details)
    alerts = [g for g in gear_list if g.get("alert") or g.get("status") in ("warning", "retire")]

    # Date formatting
    m_dt = datetime.fromisoformat(week["week_monday"]).date()
    s_dt = datetime.fromisoformat(week["week_sunday"]).date()
    date_label = f"{m_dt.strftime('%d/%m')} – {s_dt.strftime('%d/%m/%Y')}"

    total_h = week["total_time_sec"] / 3600.0
    run_km = week["run_dist_km"]
    bike_km = week["bike_dist_km"]
    swim_m = week["swim_dist_m"]
    strength_s = week["strength_time_sec"]
    effort = round(week["total_relative_effort"])
    act_count = len(week["activities"])

    ratio = acwr.get("acwr_ratio")
    acwr_str = f"{ratio:.2f}" if ratio is not None else "N/A"
    acwr_zone = acwr_label(acwr.get("status"), short=True)

    tsb = cur_perf.get("tsb", 0.0)
    ctl = cur_perf.get("ctl", 0.0)
    atl = cur_perf.get("atl", 0.0)
    form_status = tsb_label(cur_perf.get("status"))

    lines = [
        "📊 *ΕΒΔΟΜΑΔΙΑΙΑ ΑΝΑΣΚΟΠΗΣΗ STAMINAI*",
        f"🗓️ *Εβδομάδα:* {date_label}",
        "━━━━━━━━━━━━━━━━━━━━",
        "⏱️ *Προπονητικός Όγκος:*",
        f"• Συνολικός Χρόνος: *{total_h:.1f} ώρες* ({act_count} προπονήσεις)",
        f"• 🔥 Relative Effort: *{effort}*",
        f"• 🏃 Τρέξιμο: *{run_km:.1f} km*",
        f"• 🚴 Ποδηλασία: *{bike_km:.1f} km*" + (f" (+{round(week['total_elevation_m'])}m)" if week['total_elevation_m'] > 0 else ""),
        f"• 🏊 Κολύμβηση: *{swim_m:.0f} m*",
    ]
    if strength_s > 0:
        lines.append(f"• 🏋️ Ενδυνάμωση: *{strength_s / 3600.0:.1f} ώρες*")

    lines.append("\n📈 *Φόρμα & Επιβάρυνση (Banister & ACWR):*")
    lines.append(f"• ⚡ ACWR Ratio: *{acwr_str}* ({acwr_zone})")
    lines.append(f"• 🏋️ Fitness (CTL): *{ctl:.1f}* | ⚡ Fatigue (ATL): *{atl:.1f}*")
    lines.append(f"• 🎯 Form (TSB): *{tsb:+.1f}* — _{form_status}_")

    if garmin_week:
        lines.append("\n🔋 *Βιομετρικά Garmin (Μέσοι Όροι):*")
        if garmin_week.get("total_sleep_h"):
            nap = f" (+{garmin_week['total_nap_h']:.1f}h naps)" if garmin_week.get("total_nap_h") else ""
            lines.append(f"• 😴 Ύπνος: *{garmin_week['total_sleep_h']:.1f} ώρες*{nap}")
        if garmin_week.get("avg_hrv"):
            hrv_val = garmin_week["avg_hrv"]
            b_hrv = baselines.get("hrv", {})
            b_range = f" (Βάση: {b_hrv['normal_range'][0]:.0f}-{b_hrv['normal_range'][1]:.0f}ms)" if b_hrv.get("normal_range") else ""
            lines.append(f"• 💓 HRV: *{hrv_val} ms*{b_range}")
        if garmin_week.get("avg_rhr"):
            lines.append(f"• 🫀 Resting HR: *{garmin_week['avg_rhr']} bpm*")

    if alerts:
        lines.append("\n👟 *Ειδοποιήσεις Εξοπλισμού:*")
        for g in alerts:
            lines.append(f"• ⚠️ *{gear_display_name(g)}:* {g['total_km']:.0f}km / {g['threshold_km']:.0f}km (απομένουν {g['remaining_km']:.0f}km)")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("🚀 Έτοιμοι για τη νέα προπονητική εβδομάδα!")

    text = "\n".join(lines)
    meta = {
        "week_key": target_key,
        "total_hours": round(total_h, 1),
        "relative_effort": effort,
        "ctl": ctl,
        "atl": atl,
        "tsb": tsb,
        "acwr": ratio,
    }
    return text, meta


def _dispatch_backoff_active(kind: str) -> bool:
    """True while a failed `kind` dispatch is still inside its retry backoff window."""
    failed_at = _LAST_DISPATCH_FAILURE.get(kind)
    return failed_at is not None and (time.time() - failed_at) < TELEGRAM_DISPATCH_RETRY_BACKOFF_SEC


def _record_dispatch_failure(kind: str, detail: Any) -> None:
    _LAST_DISPATCH_FAILURE[kind] = time.time()
    logger.warning(
        "%s Telegram dispatch failed (retrying in %ss): %s",
        kind.capitalize(),
        TELEGRAM_DISPATCH_RETRY_BACKOFF_SEC,
        redact_token(detail),
    )


def run_daily_dispatch_check() -> bool:
    """
    Check if the current Athens time has reached TELEGRAM_DAILY_DISPATCH_TIME,
    and if so, automatically send tomorrow's workout briefing once per day.
    On Sunday evenings after TELEGRAM_WEEKLY_DISPATCH_TIME, also dispatches
    the executive weekly recap.

    A failed attempt (send error or exception while building the brief) is
    retried only after TELEGRAM_DISPATCH_RETRY_BACKOFF_SEC, not on every loop.
    """
    global _LAST_DISPATCHED_DATE
    from src.config import TELEGRAM_DAILY_DISPATCH_TIME, TELEGRAM_WEEKLY_DISPATCH_TIME
    from src.storage.activity_store import get_sync_state, init_db, set_sync_state

    now_dt = now_local()
    today_str = now_dt.strftime("%Y-%m-%d")
    current_hhmm = now_dt.strftime("%H:%M")

    target_time = (TELEGRAM_DAILY_DISPATCH_TIME or TELEGRAM_DAILY_DISPATCH_FALLBACK).strip()
    weekly_target_time = (TELEGRAM_WEEKLY_DISPATCH_TIME or TELEGRAM_WEEKLY_DISPATCH_FALLBACK).strip()

    conn = None
    last_sent = _LAST_DISPATCHED_DATE
    last_weekly_sent = None
    dispatched_any = False
    try:
        try:
            conn = init_db()
            persisted = get_sync_state(conn, "telegram_last_dispatched_date")
            if persisted:
                last_sent = persisted
            last_weekly_sent = get_sync_state(conn, "telegram_last_weekly_dispatched_date")
        except Exception as e:
            logger.warning("Could not read dispatch sync state from store: %s", e)

        # 1. Daily Next-Day Workout Briefing
        if current_hhmm >= target_time and last_sent != today_str and not _dispatch_backoff_active("daily"):
            logger.info("Executing daily automated Telegram dispatch for tomorrow's workout...")
            target_date = today_local() + timedelta(days=1)
            try:
                brief, _ = build_daily_brief(target_date)
                res = send_telegram_message(brief)
            except Exception as e:
                res = {"success": False, "detail": f"{type(e).__name__}: {e}"}
            if res.get("success"):
                _LAST_DISPATCHED_DATE = today_str
                _LAST_DISPATCH_FAILURE.pop("daily", None)
                if conn:
                    try:
                        set_sync_state(conn, "telegram_last_dispatched_date", today_str)
                    except Exception as e:
                        logger.warning("Could not persist telegram_last_dispatched_date: %s", e)
                logger.info("Daily Telegram dispatch sent successfully for %s", target_date)
                dispatched_any = True
            else:
                _record_dispatch_failure("daily", res.get("detail"))

        # 2. Sunday Evening Weekly Summary Briefing (weekday 6 == Sunday)
        if (
            now_dt.weekday() == 6
            and current_hhmm >= weekly_target_time
            and last_weekly_sent != today_str
            and not _dispatch_backoff_active("weekly")
        ):
            logger.info("Executing weekly Sunday automated Telegram summary dispatch...")
            try:
                weekly_brief, _ = build_weekly_summary()
                w_res = send_telegram_message(weekly_brief)
            except Exception as e:
                w_res = {"success": False, "detail": f"{type(e).__name__}: {e}"}
            if w_res.get("success"):
                _LAST_DISPATCH_FAILURE.pop("weekly", None)
                if conn:
                    try:
                        set_sync_state(conn, "telegram_last_weekly_dispatched_date", today_str)
                    except Exception as e:
                        logger.warning("Could not persist telegram_last_weekly_dispatched_date: %s", e)
                logger.info("Weekly Sunday Telegram summary sent successfully.")
                dispatched_any = True
            else:
                _record_dispatch_failure("weekly", w_res.get("detail"))
    finally:
        if conn:
            conn.close()
    return dispatched_any
