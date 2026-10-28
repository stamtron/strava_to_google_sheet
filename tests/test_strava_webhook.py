"""
Unit tests for Strava Webhook endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.server import app


@pytest.fixture
def verify_token(monkeypatch):
    """A real (non-placeholder) verify token, as a configured deployment has."""
    monkeypatch.setattr("src.api.server.STRAVA_WEBHOOK_VERIFY_TOKEN", "verify_tok_789")
    return "verify_tok_789"


def _handshake(client, token):
    return client.get(
        "/api/strava/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": token,
            "hub.challenge": "test_challenge_abc_123",
        },
    )


def test_strava_webhook_handshake(verify_token):
    resp = _handshake(TestClient(app), verify_token)
    assert resp.status_code == 200
    assert resp.json() == {"hub.challenge": "test_challenge_abc_123"}


def test_strava_webhook_handshake_bad_token(verify_token):
    resp = _handshake(TestClient(app), "WRONG_TOKEN")
    assert resp.status_code == 403


def test_strava_webhook_handshake_rejects_default_placeholder(monkeypatch):
    # The shipped default is public; a server still using it must not subscribe.
    monkeypatch.setattr("src.api.server.STRAVA_WEBHOOK_VERIFY_TOKEN", "STRAVA_WEBHOOK_SECRET")
    resp = _handshake(TestClient(app), "STRAVA_WEBHOOK_SECRET")
    assert resp.status_code == 403
    assert "not configured" in resp.json()["detail"]


def test_strava_webhook_event_post(monkeypatch):
    client = TestClient(app)
    processed = []

    monkeypatch.setattr("src.config.STRAVA_WEBHOOK_SECRET_PARAM", "strava_sec_456")
    monkeypatch.setattr(
        "src.api.server._process_strava_webhook_activity",
        lambda obj_id: processed.append(obj_id),
    )

    event_payload = {
        "aspect_type": "create",
        "event_time": 1549560134,
        "object_id": 999999999,
        "object_type": "activity",
        "owner_id": 12345,
        "subscription_id": 1,
    }
    resp = client.post("/api/strava/webhook?secret=strava_sec_456", json=event_payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    # BackgroundTasks run after response returns in TestClient
    assert processed == [999999999]


def test_strava_webhook_secret_param_validation(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr("src.config.STRAVA_WEBHOOK_SECRET_PARAM", "strava_sec_456")

    event_payload = {
        "aspect_type": "create",
        "object_id": 12345,
        "object_type": "activity",
    }

    # Missing or wrong secret -> 403
    resp = client.post("/api/strava/webhook", json=event_payload)
    assert resp.status_code == 403

    resp = client.post("/api/strava/webhook?secret=wrong", json=event_payload)
    assert resp.status_code == 403

    # Matching secret -> 200
    resp = client.post("/api/strava/webhook?secret=strava_sec_456", json=event_payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_strava_webhook_rejects_events_when_secret_unset(monkeypatch):
    # Fail closed: without a configured secret any caller could inject events.
    monkeypatch.setattr("src.config.STRAVA_WEBHOOK_SECRET_PARAM", "")
    processed = []
    monkeypatch.setattr(
        "src.api.server._process_strava_webhook_activity",
        lambda obj_id: processed.append(obj_id),
    )
    resp = TestClient(app).post(
        "/api/strava/webhook?secret=anything",
        json={"aspect_type": "create", "object_id": 1, "object_type": "activity"},
    )
    assert resp.status_code == 403
    assert processed == []
