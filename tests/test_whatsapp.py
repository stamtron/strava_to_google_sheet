"""
Unit tests for WhatsApp Dispatcher and Next-Day Briefing.
"""

from datetime import date
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse
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


def test_callmebot_percent_encodes_the_leading_plus(monkeypatch):
    """A raw "+" in a query string decodes to a space, losing the country code."""
    monkeypatch.setattr("src.integrations.whatsapp.WHATSAPP_PROVIDER", "callmebot")
    monkeypatch.setattr("src.integrations.whatsapp.CALLMEBOT_PHONE", "+30 691-2345678")
    monkeypatch.setattr("src.integrations.whatsapp.CALLMEBOT_API_KEY", "k")

    mock_get = MagicMock()
    mock_get.return_value.status_code = 200
    mock_get.return_value.text = "Message queued"

    with patch("requests.get", mock_get):
        send_whatsapp_message("hi")

    url = mock_get.call_args[0][0]
    assert "phone=%2B306912345678" in url
    assert "phone=+" not in url
    assert parse_qs(urlparse(url).query)["phone"] == ["+306912345678"]


def test_twilio_prefixes_bare_fallback_recipient(monkeypatch):
    """CALLMEBOT_PHONE is a bare number; Twilio rejects it without the channel prefix."""
    monkeypatch.setattr("src.integrations.whatsapp.WHATSAPP_PROVIDER", "twilio")
    monkeypatch.setattr("src.integrations.whatsapp.TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr("src.integrations.whatsapp.TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr("src.integrations.whatsapp.TWILIO_WHATSAPP_TO", "")
    monkeypatch.setattr("src.integrations.whatsapp.CALLMEBOT_PHONE", "306912345678")

    mock_post = MagicMock()
    mock_post.return_value.status_code = 201

    with patch("requests.post", mock_post):
        res = send_whatsapp_message("hi")

    assert res["success"] is True
    assert mock_post.call_args.kwargs["data"]["To"] == "whatsapp:+306912345678"


def test_twilio_reports_missing_recipient(monkeypatch):
    monkeypatch.setattr("src.integrations.whatsapp.WHATSAPP_PROVIDER", "twilio")
    monkeypatch.setattr("src.integrations.whatsapp.TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr("src.integrations.whatsapp.TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr("src.integrations.whatsapp.TWILIO_WHATSAPP_TO", "")
    monkeypatch.setattr("src.integrations.whatsapp.CALLMEBOT_PHONE", "")

    with patch("requests.post") as mock_post:
        res = send_whatsapp_message("hi")

    assert res["success"] is False
    assert "recipient" in res["detail"].lower()
    mock_post.assert_not_called()


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
