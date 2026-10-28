"""
API-level tests: dashboard degradation, CSRF/bearer guard, request size limits
and the error paths that must not leak internals. Everything external is
monkeypatched, so the suite stays offline.
"""

import pytest
from fastapi.testclient import TestClient

from src.api import server
from src.config import CHAT_MESSAGE_MAX_CHARS, CHAT_WEEK_CONTEXT_MAX_CHARS
from tests.conftest import make_activity

CLIENT_HEADERS = {"X-StaminAI-Client": "web"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("src.config.API_AUTH_TOKEN", "")
    return TestClient(server.app)


@pytest.fixture
def offline_dashboard(monkeypatch):
    """Dashboard inputs with Strava, Garmin and weather stubbed out."""
    acts = [
        make_activity(1, "2026-08-03", "Run"),
        make_activity(2, "2026-08-05", "Ride"),
        make_activity(3, "2026-08-10", "Run"),
    ]
    monkeypatch.setattr(server, "_get_summaries", lambda count: (acts, {}))
    monkeypatch.setattr(server, "_load_garmin_cache", dict)
    monkeypatch.setattr(server, "get_weekly_health_summaries", lambda ranges, max_fetch=None: {})
    return acts


# --- /api/dashboard -----------------------------------------------------------


def test_dashboard_survives_weather_failure(client, offline_dashboard, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("open-meteo down")

    monkeypatch.setattr(server, "get_weather_outlook", boom)
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["weather"] == []
    assert body["activities_total"] == 3
    assert "performance" in body and "predictions" in body


def test_dashboard_analytics_failure_is_a_clean_500(client, offline_dashboard, monkeypatch):
    monkeypatch.setattr(server, "get_weather_outlook", lambda **_k: [])

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(server, "calculate_fitness_fatigue", boom)
    resp = client.get("/api/dashboard")
    assert resp.status_code == 500
    assert "secret internal detail" not in resp.text


# --- CSRF header and bearer token --------------------------------------------


@pytest.fixture
def stub_coach(monkeypatch):
    monkeypatch.setattr(server, "generate_weekly_coaching_insights", lambda **_k: {"ok": True})


def test_post_without_client_header_is_forbidden(client, stub_coach):
    resp = client.post("/api/ai/coach", json={"week_summary": {}})
    assert resp.status_code == 403
    assert "X-StaminAI-Client" in resp.json()["detail"]


def test_post_with_wrong_client_header_is_forbidden(client, stub_coach):
    resp = client.post("/api/ai/coach", json={"week_summary": {}}, headers={"X-StaminAI-Client": "evil"})
    assert resp.status_code == 403


def test_post_with_client_header_is_allowed(client, stub_coach):
    resp = client.post("/api/ai/coach", json={"week_summary": {}}, headers=CLIENT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_delete_requires_client_header(client, monkeypatch):
    monkeypatch.setattr(server, "forget_remembered_fact", lambda fact_id: True)
    assert client.delete("/api/coach/memory/abc").status_code == 403
    assert client.delete("/api/coach/memory/abc", headers=CLIENT_HEADERS).status_code == 200


def test_get_routes_do_not_need_the_header(client):
    assert client.get("/api/health").status_code == 200


def test_bearer_token_required_when_configured(client, stub_coach, monkeypatch):
    monkeypatch.setattr("src.config.API_AUTH_TOKEN", "tok_123")
    payload = {"week_summary": {}}

    resp = client.post("/api/ai/coach", json=payload, headers=CLIENT_HEADERS)
    assert resp.status_code == 401

    resp = client.post(
        "/api/ai/coach", json=payload, headers={**CLIENT_HEADERS, "Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401

    resp = client.post(
        "/api/ai/coach", json=payload, headers={**CLIENT_HEADERS, "Authorization": "Bearer tok_123"}
    )
    assert resp.status_code == 200


def test_token_alone_does_not_replace_client_header(client, stub_coach, monkeypatch):
    monkeypatch.setattr("src.config.API_AUTH_TOKEN", "tok_123")
    resp = client.post("/api/ai/coach", json={"week_summary": {}}, headers={"Authorization": "Bearer tok_123"})
    assert resp.status_code == 403


def test_webhooks_are_exempt_from_client_header_but_need_their_secret(client, monkeypatch):
    monkeypatch.setattr("src.config.STRAVA_WEBHOOK_SECRET_PARAM", "")
    monkeypatch.setattr("src.config.TELEGRAM_WEBHOOK_SECRET", "")
    # No client header, and the secrets are unset: rejected by the webhook
    # itself (fail closed), not by the CSRF guard.
    resp = client.post("/api/strava/webhook", json={"object_type": "activity"})
    assert resp.status_code == 403
    assert "not configured" in resp.json()["detail"]
    resp = client.post("/api/notifications/telegram/webhook", json={"update_id": 1})
    assert resp.status_code == 403
    assert "not configured" in resp.json()["detail"]


# --- request size limits -----------------------------------------------------


def test_chat_rejects_oversized_message(client, monkeypatch):
    called = []
    monkeypatch.setattr(server, "coach_chat", lambda **k: called.append(k))
    resp = client.post(
        "/api/ai/chat", json={"message": "x" * (CHAT_MESSAGE_MAX_CHARS + 1)}, headers=CLIENT_HEADERS
    )
    assert resp.status_code == 422
    assert called == []


def test_chat_rejects_oversized_week_context(client, monkeypatch):
    monkeypatch.setattr(server, "coach_chat", lambda **k: {"reply": "hi"})
    resp = client.post(
        "/api/ai/chat",
        json={"message": "hi", "week_context": {"blob": "y" * CHAT_WEEK_CONTEXT_MAX_CHARS}},
        headers=CLIENT_HEADERS,
    )
    assert resp.status_code == 422


def test_chat_accepts_normal_turn(client, monkeypatch):
    monkeypatch.setattr(server, "coach_chat", lambda **k: {"reply": "hi", "session_id": "s1"})
    resp = client.post("/api/ai/chat", json={"message": "how was my week?"}, headers=CLIENT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["reply"] == "hi"


# --- error paths ------------------------------------------------------------


def test_ai_coach_error_does_not_leak_exception_text(client, monkeypatch):
    def boom(**_k):
        raise RuntimeError("API key sk-live-123 rejected")

    monkeypatch.setattr(server, "generate_weekly_coaching_insights", boom)
    resp = client.post("/api/ai/coach", json={"week_summary": {}}, headers=CLIENT_HEADERS)
    assert resp.status_code == 500
    assert "sk-live-123" not in resp.text


def test_compliance_sheet_failure_is_a_clean_500(client, monkeypatch):
    server._PLANNED_WORKOUT_CACHE.clear()

    def boom(_d):
        raise RuntimeError("sheets quota")

    monkeypatch.setattr(server, "get_planned_workout_for_date", boom)
    resp = client.get("/api/compliance", params={"target_date": "2026-08-10"})
    assert resp.status_code == 500
    assert "sheets quota" not in resp.text


def test_compliance_bad_date_is_400(client):
    resp = client.get("/api/compliance", params={"target_date": "10/08/2026"})
    assert resp.status_code == 400


def test_gear_failure_is_a_clean_500(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db locked")

    monkeypatch.setattr("src.analytics.gear.extract_gear_summary", boom)
    monkeypatch.setattr(server, "_load_cache", lambda: {"activities": [make_activity(1, "2026-08-03")], "details": {}})
    resp = client.get("/api/gear")
    assert resp.status_code == 500
    assert "db locked" not in resp.text


def test_history_status_failure_is_a_clean_500(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(server, "init_db", boom)
    resp = client.get("/api/history/status")
    assert resp.status_code == 500
    assert "disk full" not in resp.text
