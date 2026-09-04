"""
Unit tests for Telegram Dispatcher and Next-Day Briefing.
"""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from src.integrations.telegram import format_next_day_brief, send_telegram_message


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
    resp = client.post("/api/notifications/telegram/next-day", json={"dry_run": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "preview" in data
    assert data["provider"] == "dry-run"


def test_whatsapp_deprecated_api_endpoint_routes_to_telegram():
    from fastapi.testclient import TestClient
    from src.api.server import app

    client = TestClient(app)
    resp = client.post("/api/notifications/whatsapp/next-day", json={"dry_run": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "preview" in data

