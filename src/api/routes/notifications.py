"""Telegram: daily and weekly brief dispatch, and the bot's webhook."""

import asyncio
import hmac
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from src.timeutil import today_local

logger = logging.getLogger(__name__)
router = APIRouter()


class TelegramNextDayRequest(BaseModel):
    target_date: str | None = None  # YYYY-MM-DD, defaults to tomorrow
    custom_tip: str | None = None
    dry_run: bool = False


@router.post("/api/notifications/telegram/next-day")
def send_telegram_next_day_workout_notification(req: TelegramNextDayRequest = TelegramNextDayRequest()):
    """
    Extract tomorrow's planned workout from Final Surge, combine with Athens
    weather forecast, Garmin recovery biometrics and an AI tip, and dispatch to athlete's Telegram.
    """
    if req.target_date:
        try:
            t_date = date.fromisoformat(req.target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.") from None
    else:
        t_date = today_local() + timedelta(days=1)

    # Imported here so tests can monkeypatch send_telegram_message.
    from src.integrations.telegram import build_daily_brief, send_telegram_message

    brief_text, meta = build_daily_brief(t_date, custom_tip=req.custom_tip)

    if req.dry_run:
        return {
            "success": True,
            "target_date": t_date.isoformat(),
            "preview": brief_text,
            "workout_info": meta["workout_info"],
            "weather_info": meta["weather_info"],
            "recovery_info": meta["recovery_info"],
            "readiness_info": meta["readiness_info"],
            "provider": "dry-run",
        }

    dispatch_res = send_telegram_message(brief_text)
    return {
        "success": dispatch_res.get("success", False),
        "target_date": t_date.isoformat(),
        "preview": brief_text,
        "dispatch": dispatch_res,
        "workout_info": meta["workout_info"],
        "weather_info": meta["weather_info"],
        "recovery_info": meta["recovery_info"],
        "readiness_info": meta["readiness_info"],
    }


@router.post("/api/notifications/telegram/webhook")
async def telegram_webhook_handler(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    """
    Receive webhook updates directly from Telegram Bot API without polling.
    Allows instant processing of athlete commands (/today, /tomorrow, /recovery, /coach, etc.).
    """
    from src.config import TELEGRAM_WEBHOOK_SECRET

    # Fail closed: without a secret anyone could drive the bot as the athlete.
    if not TELEGRAM_WEBHOOK_SECRET:
        logger.warning("Rejected Telegram webhook: TELEGRAM_WEBHOOK_SECRET is not configured.")
        raise HTTPException(status_code=403, detail="Webhook secret token is not configured.")
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, TELEGRAM_WEBHOOK_SECRET
    ):
        logger.warning("Rejected Telegram webhook: invalid secret token header.")
        raise HTTPException(status_code=403, detail="Invalid webhook secret token.")

    from src.integrations.telegram import process_incoming_update
    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    if not isinstance(update_data, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    # Run processing in background thread
    await asyncio.to_thread(process_incoming_update, update_data)
    return {"ok": True}


@router.post("/api/notifications/telegram/today")
def send_telegram_today_workout_notification(req: TelegramNextDayRequest = TelegramNextDayRequest()):
    """Dispatch today's planned workout brief + weather + AI tip to athlete's Telegram."""
    if not req.target_date:
        req.target_date = today_local().isoformat()
    return send_telegram_next_day_workout_notification(req)


@router.post("/api/notifications/telegram/weekly")
def send_telegram_weekly_summary_notification():
    """Dispatch executive weekly summary to athlete's Telegram."""
    from src.integrations.telegram import build_weekly_summary, send_telegram_message

    brief, meta = build_weekly_summary()
    res = send_telegram_message(brief)
    return {
        "success": res.get("success", False),
        "preview": brief,
        "dispatch": res,
        "meta": meta,
    }
