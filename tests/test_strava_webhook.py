"""
Unit tests for Strava Webhook endpoints.
"""

from fastapi.testclient import TestClient
from src.api.server import app


def test_strava_webhook_handshake():
    client = TestClient(app)
    resp = client.get(
        "/api/strava/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "STRAVA_WEBHOOK_SECRET",
            "hub.challenge": "test_challenge_abc_123",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"hub.challenge": "test_challenge_abc_123"}


def test_strava_webhook_handshake_bad_token():
    client = TestClient(app)
    resp = client.get(
        "/api/strava/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG_TOKEN",
            "hub.challenge": "test_challenge",
        },
    )
    assert resp.status_code == 403


def test_strava_webhook_event_post(monkeypatch):
    client = TestClient(app)
    event_payload = {
        "aspect_type": "create",
        "event_time": 1549560134,
        "object_id": 999999999,
        "object_type": "activity",
        "owner_id": 12345,
        "subscription_id": 1,
    }
    resp = client.post("/api/strava/webhook", json=event_payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
