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
    processed = []

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
    resp = client.post("/api/strava/webhook", json=event_payload)
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

