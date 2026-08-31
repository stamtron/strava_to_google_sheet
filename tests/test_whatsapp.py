"""
Unit tests for WhatsApp Dispatcher and Next-Day Briefing.
"""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from src.integrations.whatsapp import format_next_day_brief, send_whatsapp_message


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


def test_send_whatsapp_console_fallback(monkeypatch):
    monkeypatch.setattr("src.integrations.whatsapp.WHATSAPP_PROVIDER", "console")
    res = send_whatsapp_message("Test briefing")
    assert res["success"] is True
    assert res["provider"] == "console"


def test_send_whatsapp_callmebot_mock(monkeypatch):
    monkeypatch.setattr("src.integrations.whatsapp.WHATSAPP_PROVIDER", "callmebot")
    monkeypatch.setattr("src.integrations.whatsapp.CALLMEBOT_PHONE", "+306912345678")
    monkeypatch.setattr("src.integrations.whatsapp.CALLMEBOT_API_KEY", "test_key_123")

    mock_get = MagicMock()
    mock_get.return_value.status_code = 200
    mock_get.return_value.text = "Message queued"

    with patch("requests.get", mock_get):
        res = send_whatsapp_message("Workout alert!")
        assert res["success"] is True
        assert res["provider"] == "callmebot"
        assert mock_get.call_count == 1
