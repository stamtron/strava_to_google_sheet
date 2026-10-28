"""
Unit tests for Telegram Dispatcher and Next-Day Briefing.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.telegram import format_next_day_brief, send_telegram_message


@pytest.fixture(autouse=True)
def default_telegram_config(monkeypatch):
    monkeypatch.setattr("src.config.TELEGRAM_CHAT_ID", "12345678")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "12345678")


def test_format_next_day_brief_with_weather_and_tip():
    t_date = date(2026, 9, 1)  # Tuesday
    workout = "🚴 1h30m Zone 2 Ride + 4x30s cadence drills"
    weather = {
        "condition": "Mainly clear",
        "icon": "🌤️",
        "temp_max_c": 31.0,
        "temp_min_c": 22.0,
        "precipitation_mm": 0.0,
        "precip_probability_pct": 5,
        "wind_speed_max_kmh": 18.0,
    }
    tip = "Maintain steady cadence of 85-90 rpm."

    brief = format_next_day_brief(
        target_date=t_date,
        workout_text=workout,
        weather_info=weather,
        coach_tip=tip,
    )

    assert "ΤΡΙΤΗ" in brief
    assert "01/09/2026" in brief
    assert "Zone 2 Ride" in brief
    assert "31°C" in brief
    assert "Mainly clear" in brief
    assert "Maintain steady cadence" in brief


def test_send_telegram_dry_run_when_unset(monkeypatch):
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "")
    res = send_telegram_message("Test briefing")
    assert res["success"] is True
    assert res["provider"] == "telegram (dry-run)"
    assert "unset" in res["detail"].lower()


def test_send_telegram_empty_message():
    res = send_telegram_message("   ")
    assert res["success"] is False
    assert "empty" in res["detail"].lower()


def test_send_telegram_success_with_markdown(monkeypatch):
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", "fake_bot_token")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "123456789")

    mock_post = MagicMock()
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"ok": True}

    with patch("requests.post", mock_post):
        res = send_telegram_message("Workout alert!")
        assert res["success"] is True
        assert res["provider"] == "telegram"
        assert res["detail"] == "Message delivered."
        assert mock_post.call_count == 1
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["chat_id"] == "123456789"
        assert call_kwargs["json"]["parse_mode"] == "Markdown"


def test_send_telegram_retries_as_plain_text_on_parse_error(monkeypatch):
    """When workout text has unescaped characters, Telegram 400s; we must retry plain text."""
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", "fake_bot_token")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "123456789")

    fail_resp = MagicMock()
    fail_resp.status_code = 400
    fail_resp.text = '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"ok": True}

    mock_post = MagicMock(side_effect=[fail_resp, ok_resp])

    with patch("requests.post", mock_post):
        res = send_telegram_message("Unclosed *markdown _workout")
        assert res["success"] is True
        assert res["provider"] == "telegram"
        assert "plain text fallback" in res["detail"]
        assert mock_post.call_count == 2
        # Second call should not have parse_mode
        second_call_kwargs = mock_post.call_args_list[1][1]
        assert "parse_mode" not in second_call_kwargs["json"]


def test_send_telegram_handles_api_failure(monkeypatch):
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", "fake_bot_token")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "123456789")

    mock_post = MagicMock()
    mock_post.return_value.status_code = 403
    mock_post.return_value.text = '{"ok":false,"description":"Forbidden: bot was blocked by the user"}'

    with patch("requests.post", mock_post):
        res = send_telegram_message("Workout alert!")
        assert res["success"] is False
        assert "403" in res["detail"]


def test_send_telegram_handles_request_exception(monkeypatch):
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", "fake_bot_token")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "123456789")

    with patch("requests.post", side_effect=ConnectionError("Network down")):
        res = send_telegram_message("Workout alert!")
        assert res["success"] is False
        assert "Network down" in res["detail"]


def test_brief_survives_null_weather_values():
    """Open-Meteo returns null inside the daily arrays for days it has no value."""
    brief = format_next_day_brief(
        target_date=date(2026, 9, 1),
        workout_text="Easy 5k",
        weather_info={
            "condition": "Clear",
            "icon": "☀️",
            "temp_max_c": 30.0,
            "temp_min_c": 20.0,
            "precipitation_mm": None,
            "precip_probability_pct": None,
            "wind_speed_max_kmh": None,
        },
    )
    assert "Easy 5k" in brief
    assert "20°C – 30°C" in brief


def test_unreadable_plan_is_not_reported_as_a_rest_day():
    """A swallowed sheet error must never tell the athlete to rest."""
    brief = format_next_day_brief(
        target_date=date(2026, 9, 1),
        workout_text="",
        lookup_error="Failed to read Google Sheet: [Errno 1] Operation not permitted",
    )
    assert "Rest Day" not in brief
    assert "⚠️" in brief
    assert "Operation not permitted" in brief


def test_genuinely_empty_plan_still_reads_as_a_rest_day():
    brief = format_next_day_brief(target_date=date(2026, 9, 1), workout_text="")
    assert "Rest Day" in brief
    assert "⚠️" not in brief


def test_telegram_api_endpoint_dry_run():
    from fastapi.testclient import TestClient

    from src.api.server import app

    client = TestClient(app)
    resp = client.post("/api/notifications/telegram/next-day", json={"dry_run": True}, headers={"X-StaminAI-Client": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "preview" in data
    assert data["provider"] == "dry-run"


def test_telegram_webhook_secret_token_validation(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.server import app

    monkeypatch.setattr("src.config.TELEGRAM_WEBHOOK_SECRET", "super_secret_token_123")
    client = TestClient(app)

    # 1. Missing secret token header -> 403
    resp = client.post("/api/notifications/telegram/webhook", json={"update_id": 1})
    assert resp.status_code == 403
    assert "Invalid webhook secret token" in resp.json()["detail"]

    # 2. Wrong secret token header -> 403
    resp = client.post(
        "/api/notifications/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
    )
    assert resp.status_code == 403

    # 3. Valid secret token header -> 200
    resp = client.post(
        "/api/notifications/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "super_secret_token_123"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_telegram_webhook_rejects_when_secret_unset(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.server import app

    # Fail closed: no configured secret means nobody may drive the bot.
    monkeypatch.setattr("src.config.TELEGRAM_WEBHOOK_SECRET", "")
    client = TestClient(app)
    resp = client.post(
        "/api/notifications/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "anything"},
    )
    assert resp.status_code == 403
    assert "not configured" in resp.json()["detail"]


def test_handle_telegram_command_help():
    from src.integrations.telegram import handle_telegram_command
    res = handle_telegram_command("/help")
    assert "StaminAI Telegram Assistant" in res
    assert "/today" in res
    assert "/sync" in res
    assert "/gear" in res


def test_handle_telegram_command_unconfigured_chat_id(monkeypatch):
    from src.integrations.telegram import handle_telegram_command
    monkeypatch.setattr("src.config.TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "")
    res = handle_telegram_command("/today", chat_id="12345")
    assert "Δεν έχεις δικαίωμα πρόσβασης" in res or "Δεν έχει ρυθμιστεί" in res


def test_handle_telegram_command_unauthorized(monkeypatch):
    from src.integrations.telegram import handle_telegram_command
    monkeypatch.setattr("src.config.TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_CHAT_ID", "12345")
    res = handle_telegram_command("/today", chat_id="99999")
    assert "Δεν έχεις δικαίωμα πρόσβασης" in res


def test_telegram_today_api_endpoint_dry_run():
    from fastapi.testclient import TestClient

    from src.api.server import app

    client = TestClient(app)
    resp = client.post("/api/notifications/telegram/today", json={"dry_run": True}, headers={"X-StaminAI-Client": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "preview" in data
    assert data["provider"] == "dry-run"


def test_telegram_weekly_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.server import app

    monkeypatch.setattr("src.integrations.telegram.build_weekly_summary", lambda week_key=None: ("Weekly Report Preview", {"week_key": "2026-W41"}))
    monkeypatch.setattr("src.integrations.telegram.send_telegram_message", lambda text: {"success": True, "provider": "dry-run"})

    client = TestClient(app)
    resp = client.post("/api/notifications/telegram/weekly", headers={"X-StaminAI-Client": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "preview" in data
    assert "Weekly Report Preview" in data["preview"]



def test_format_next_day_brief_with_recovery():
    t_date = date(2026, 9, 1)
    workout = "🏃 8x400m @ 3:55"
    recovery = {
        "available": True,
        "recovery_status": "compromised",
        "sleep_hours": 5.8,
        "hrv_last_night": 42,
        "resting_hr": 58,
        "body_battery_latest": 35,
    }
    readiness = {
        "needs_modulation": True,
        "advice_key": "compromised_high_intensity",
        "flags": ["low_sleep"],
    }
    brief = format_next_day_brief(
        target_date=t_date,
        workout_text=workout,
        recovery_info=recovery,
        readiness_info=readiness,
    )
    assert "Αποκατάσταση (Garmin)" in brief
    assert "5.8h" in brief
    assert "42ms" in brief
    assert "58 bpm" in brief
    assert "35%" in brief
    assert "Συνιστάται μείωση της έντασης" in brief
    assert "(low_sleep)" in brief


def test_handle_telegram_command_recovery(monkeypatch):
    import src.integrations.garmin as garmin_mod
    from src.integrations.telegram import handle_telegram_command

    fake_rec = {
        "available": True,
        "recovery_status": "optimal",
        "recovery_score": 88,
        "sleep_hours": 8.1,
        "sleep_score": 85,
        "hrv_last_night": 74,
        "hrv_status": "balanced",
        "resting_hr": 47,
        "body_battery_latest": 90,
        "flags": [],
    }
    monkeypatch.setattr(garmin_mod, "get_daily_recovery_metrics", lambda d, client=None: fake_rec)

    res = handle_telegram_command("/recovery")
    assert "Ημερήσια Αποκατάσταση & Ετοιμότητα" in res
    assert "OPTIMAL" in res
    assert "8.1 ώρες" in res
    assert "74 ms" in res
    assert "47 bpm" in res
    assert "90%" in res


def test_handle_telegram_command_compliance(monkeypatch):
    import src.analytics.compliance as comp_mod
    from src.integrations.telegram import handle_telegram_command

    fake_comp = {
        "compliance_score": 95,
        "status": "evaluated",
        "planned_text": "Τρέξιμο 10 χλμ",
        "matches": [{"sport": "Run", "status": "spot_on", "planned_km": 10.0, "actual_km": 10.2,
                     "actual_pace_sec": 312.0, "pace_unit": "per_km"}],
    }
    monkeypatch.setattr(comp_mod, "evaluate_daily_compliance", lambda planned, acts: fake_comp)

    res = handle_telegram_command("/compliance 2026-09-08")
    assert "Συμμόρφωση Προπόνησης" in res
    assert "95%" in res
    assert "10.2km" in res
    assert "5:12/χλμ" in res


def test_telegram_webhook_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.server import app

    monkeypatch.setattr("src.config.TELEGRAM_WEBHOOK_SECRET", "hook_secret")
    client = TestClient(app, headers={"X-Telegram-Bot-Api-Secret-Token": "hook_secret"})
    payload = {
        "update_id": 123456,
        "message": {
            "message_id": 1,
            "chat": {"id": 999},
            "text": "/help",
        },
    }
    resp = client.post("/api/notifications/telegram/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_build_daily_brief_and_metadata(monkeypatch):
    from datetime import date

    import src.integrations.garmin as garmin_mod
    import src.integrations.sheets as sheets_mod
    import src.integrations.weather as weather_mod
    from src.integrations.telegram import build_daily_brief

    monkeypatch.setattr(
        sheets_mod,
        "get_planned_workout_for_date",
        lambda d: {"workout_text": "Easy Run 8km", "reason": None},
    )
    monkeypatch.setattr(
        weather_mod,
        "get_weather_for_date",
        lambda d: {"temp_max_c": 22.0, "temp_min_c": 16.0, "precipitation_mm": 0.0},
    )
    monkeypatch.setattr(
        garmin_mod,
        "get_daily_recovery_metrics",
        lambda d: {"available": False},
    )

    brief, meta = build_daily_brief(date(2026, 10, 6))
    assert "ΠΡΟΠΟΝΗΣΗ ΗΜΕΡΑΣ" in brief
    assert "Easy Run 8km" in brief
    assert meta["workout_info"]["workout_text"] == "Easy Run 8km"
    assert "Zone 2" in meta["coach_tip"]


def test_run_daily_dispatch_check_persists(monkeypatch, tmp_path):
    from datetime import date, datetime

    import src.integrations.telegram as tel_mod
    from src.storage.activity_store import get_sync_state, init_db

    db_path = str(tmp_path / "test_history.db")
    conn = init_db(db_path)
    conn.close()

    monkeypatch.setattr("src.storage.activity_store.HISTORY_DB_FILE", db_path)
    monkeypatch.setattr("src.config.HISTORY_DB_FILE", db_path)
    monkeypatch.setattr(tel_mod, "_LAST_DISPATCHED_DATE", None)
    monkeypatch.setattr(tel_mod, "send_telegram_message", lambda brief: {"success": True})
    monkeypatch.setattr(tel_mod, "build_daily_brief", lambda d: ("Brief text", {}))

    monkeypatch.setattr(tel_mod, "_LAST_DISPATCH_FAILURE", {})
    monkeypatch.setattr("src.config.TELEGRAM_DAILY_DISPATCH_TIME", "20:30")

    # Mock time so it is past dispatch time e.g. 21:00 >= 20:30 (a Tuesday)
    fake_now = datetime(2026, 10, 6, 21, 0, 0)
    monkeypatch.setattr(tel_mod, "now_local", lambda: fake_now)
    monkeypatch.setattr(tel_mod, "today_local", lambda: date(2026, 10, 6))

    # First run should send and persist
    sent = tel_mod.run_daily_dispatch_check()
    assert sent is True

    conn2 = init_db(db_path)
    persisted = get_sync_state(conn2, "telegram_last_dispatched_date")
    conn2.close()
    assert persisted == "2026-10-06"

    # Second run on same day should not duplicate
    sent_again = tel_mod.run_daily_dispatch_check()
    assert sent_again is False


def test_build_weekly_summary(monkeypatch):
    from src.integrations.telegram import build_weekly_summary

    monkeypatch.setattr("src.storage.activity_store.get_activities", lambda conn, limit=250: [
        {
            "id": 1,
            "sport_type": "Run",
            "distance": 10000.0,
            "moving_time": 3000,
            "suffer_score": 50,
            "start_date_local": "2026-10-10T08:00:00Z",
            "gear_id": "shoe_1",
        }
    ])
    monkeypatch.setattr("src.storage.activity_store.get_details", lambda conn: {
        "1": {"gear": {"name": "Pegasus 40"}}
    })

    brief, meta = build_weekly_summary()
    assert "ΕΒΔΟΜΑΔΙΑΙΑ ΑΝΑΣΚΟΠΗΣΗ" in brief
    assert "10.0 km" in brief
    assert meta is not None









def _setup_dispatch(monkeypatch, tmp_path, send_fn):
    from datetime import datetime

    import src.integrations.telegram as tel_mod
    from src.storage.activity_store import init_db

    db_path = str(tmp_path / "dispatch.db")
    init_db(db_path).close()
    monkeypatch.setattr("src.storage.activity_store.HISTORY_DB_FILE", db_path)
    monkeypatch.setattr("src.config.HISTORY_DB_FILE", db_path)
    monkeypatch.setattr("src.config.TELEGRAM_DAILY_DISPATCH_TIME", "20:30")
    monkeypatch.setattr(tel_mod, "_LAST_DISPATCHED_DATE", None)
    monkeypatch.setattr(tel_mod, "_LAST_DISPATCH_FAILURE", {})
    monkeypatch.setattr(tel_mod, "send_telegram_message", send_fn)
    monkeypatch.setattr(tel_mod, "build_daily_brief", lambda d: ("Brief text", {}))
    monkeypatch.setattr(tel_mod, "now_local", lambda: datetime(2026, 10, 6, 21, 0, 0))
    monkeypatch.setattr(tel_mod, "today_local", lambda: date(2026, 10, 6))
    return tel_mod


def test_dispatch_failure_backs_off_instead_of_retrying_every_loop(monkeypatch, tmp_path):
    calls = []

    def failing_send(brief):
        calls.append(brief)
        return {"success": False, "detail": "HTTP 500"}

    tel_mod = _setup_dispatch(monkeypatch, tmp_path, failing_send)
    clock = [1_000_000.0]
    monkeypatch.setattr(tel_mod.time, "time", lambda: clock[0])

    assert tel_mod.run_daily_dispatch_check() is False
    assert len(calls) == 1

    # Within the backoff window: no new attempt.
    clock[0] += tel_mod.TELEGRAM_DISPATCH_RETRY_BACKOFF_SEC - 1
    assert tel_mod.run_daily_dispatch_check() is False
    assert len(calls) == 1

    # After the window: retried, and success clears the failure marker.
    clock[0] += 2
    monkeypatch.setattr(tel_mod, "send_telegram_message", lambda brief: {"success": True})
    assert tel_mod.run_daily_dispatch_check() is True
    assert "daily" not in tel_mod._LAST_DISPATCH_FAILURE


def test_dispatch_exception_while_building_is_contained(monkeypatch, tmp_path):
    tel_mod = _setup_dispatch(monkeypatch, tmp_path, lambda brief: {"success": True})

    def boom(d):
        raise RuntimeError("sheet down")

    monkeypatch.setattr(tel_mod, "build_daily_brief", boom)
    assert tel_mod.run_daily_dispatch_check() is False
    assert "daily" in tel_mod._LAST_DISPATCH_FAILURE


def test_request_exception_detail_redacts_bot_token(monkeypatch):
    token = "123456:SECRET-token_value"
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", token)
    err = ConnectionError(f"Max retries exceeded with url: /bot{token}/sendMessage")

    with patch("requests.post", side_effect=err):
        res = send_telegram_message("Workout alert!")

    assert res["success"] is False
    assert token not in res["detail"]
    assert "SECRET" not in res["detail"]
    assert "<redacted>" in res["detail"]


def test_redact_token_strips_bot_url_even_for_unknown_token():
    from src.integrations.telegram import redact_token

    out = redact_token("https://api.telegram.org/bot999:abc/getUpdates failed")
    assert "999:abc" not in out
    assert "/bot<redacted>/getUpdates" in out


def test_poll_logs_redacted_errors(monkeypatch, caplog):
    import logging

    from src.integrations.telegram import poll_telegram_updates

    token = "777:POLLSECRET"
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", token)
    with caplog.at_level(logging.WARNING), patch(
        "requests.get", side_effect=ConnectionError(f"url: /bot{token}/getUpdates")
    ):
        offset, updates = poll_telegram_updates(offset=5, timeout=1)

    assert (offset, updates) == (5, [])
    assert "POLLSECRET" not in caplog.text


def test_split_message_breaks_at_newlines_within_limit():
    from src.integrations.telegram import split_message

    lines = [f"line {i:03d} " + "x" * 40 for i in range(100)]
    text = "\n".join(lines)
    chunks = split_message(text, limit=500)

    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    # Every chunk boundary falls on a line boundary: nothing lost or merged.
    assert "\n".join(chunks).split("\n") == lines


def test_split_message_hard_splits_long_line_outside_tags():
    from src.integrations.telegram import split_message

    # "<b>" straddles the 100-char limit (indices 98-100).
    text = "a" * 98 + "<b>bold</b>" + "c" * 100
    chunks = split_message(text, limit=100)

    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text
    # The cut moved before "<b>" rather than splitting the tag.
    assert chunks[0] == "a" * 98
    assert chunks[1].startswith("<b>bold</b>")


def test_long_message_is_sent_in_ordered_parts(monkeypatch):
    monkeypatch.setattr("src.integrations.telegram.TELEGRAM_BOT_TOKEN", "fake_bot_token")
    mock_post = MagicMock()
    mock_post.return_value.status_code = 200
    # Force a split on every newline so the ordering of parts is observable.
    monkeypatch.setattr("src.integrations.telegram.split_message", lambda t: t.split("\n"))

    with patch("requests.post", mock_post):
        res = send_telegram_message("part one\npart two\npart three")

    assert res["success"] is True
    assert res["detail"] == "Message delivered in 3 parts."
    sent = [c[1]["json"]["text"] for c in mock_post.call_args_list]
    assert sent == ["part one", "part two", "part three"]


def test_process_incoming_update_ignores_edits_and_strangers(monkeypatch):
    import src.integrations.telegram as tel_mod

    handled = []
    monkeypatch.setattr(tel_mod, "handle_telegram_command", lambda text, chat_id=None: handled.append(text) or "ok")
    monkeypatch.setattr(tel_mod, "send_telegram_message", lambda reply, chat_id=None: {"success": True})

    tel_mod.process_incoming_update({"edited_message": {"chat": {"id": 12345678}, "text": "/sync"}})
    tel_mod.process_incoming_update({"message": {"chat": {"id": 999}, "text": "/help"}})
    assert handled == []

    tel_mod.process_incoming_update({"message": {"chat": {"id": 12345678}, "text": "/help"}})
    assert handled == ["/help"]
