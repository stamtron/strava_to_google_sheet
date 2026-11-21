"""
API-level tests: dashboard degradation, CSRF/bearer guard, request size limits
and the error paths that must not leak internals. Everything external is
monkeypatched, so the suite stays offline.
"""

import pytest
from fastapi.testclient import TestClient

from src.api import server
from src.api.routes import assistant as assistant_routes
from src.api.routes import dashboard as dashboard_routes
from src.api.routes import strava as strava_routes
from src.api.routes import training as training_routes
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
    monkeypatch.setattr(dashboard_routes, "get_summaries", lambda count: (acts, {}))
    monkeypatch.setattr(dashboard_routes, "_load_garmin_cache", dict)
    monkeypatch.setattr(dashboard_routes, "get_weekly_health_summaries", lambda ranges, max_fetch=None: {})
    return acts


# --- /api/dashboard -----------------------------------------------------------


def test_dashboard_survives_weather_failure(client, offline_dashboard, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("open-meteo down")

    monkeypatch.setattr(dashboard_routes, "get_weather_outlook", boom)
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["weather"] == []
    assert body["activities_total"] == 3
    assert "performance" in body and "predictions" in body


def test_dashboard_baselines_use_whole_garmin_cache(client, offline_dashboard, monkeypatch):
    # Export-imported weeks with no activities in the loaded window still count.
    cache = {
        f"2026-0{m}-0{d}": {"summary": {"avg_rhr": 48, "avg_hrv": 72}, "fetched_at": 1.0}
        for m, d in ((3, 2), (3, 9), (4, 6), (5, 4))
    }
    monkeypatch.setattr(dashboard_routes, "_load_garmin_cache", lambda: cache)
    monkeypatch.setattr(dashboard_routes, "get_weather_outlook", lambda **_k: [])
    body = client.get("/api/dashboard").json()
    assert body["garmin"] == {}
    assert body["baselines"]["rhr"]["weeks_used"] == 4


def test_dashboard_analytics_failure_is_a_clean_500(client, offline_dashboard, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_weather_outlook", lambda **_k: [])

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(dashboard_routes, "calculate_fitness_fatigue", boom)
    resp = client.get("/api/dashboard")
    assert resp.status_code == 500
    assert "secret internal detail" not in resp.text


# --- CSRF header and bearer token --------------------------------------------


@pytest.fixture
def stub_coach(monkeypatch):
    monkeypatch.setattr(assistant_routes, "generate_weekly_coaching_insights", lambda **_k: {"ok": True})


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
    monkeypatch.setattr(assistant_routes, "forget_remembered_fact", lambda fact_id: True)
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
    monkeypatch.setattr(assistant_routes, "coach_chat", lambda **k: called.append(k))
    resp = client.post(
        "/api/ai/chat", json={"message": "x" * (CHAT_MESSAGE_MAX_CHARS + 1)}, headers=CLIENT_HEADERS
    )
    assert resp.status_code == 422
    assert called == []


def test_chat_rejects_oversized_week_context(client, monkeypatch):
    monkeypatch.setattr(assistant_routes, "coach_chat", lambda **k: {"reply": "hi"})
    resp = client.post(
        "/api/ai/chat",
        json={"message": "hi", "week_context": {"blob": "y" * CHAT_WEEK_CONTEXT_MAX_CHARS}},
        headers=CLIENT_HEADERS,
    )
    assert resp.status_code == 422


def test_chat_accepts_normal_turn(client, monkeypatch):
    monkeypatch.setattr(assistant_routes, "coach_chat", lambda **k: {"reply": "hi", "session_id": "s1"})
    resp = client.post("/api/ai/chat", json={"message": "how was my week?"}, headers=CLIENT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["reply"] == "hi"


# --- error paths ------------------------------------------------------------


def test_ai_coach_error_does_not_leak_exception_text(client, monkeypatch):
    def boom(**_k):
        raise RuntimeError("API key sk-live-123 rejected")

    monkeypatch.setattr(assistant_routes, "generate_weekly_coaching_insights", boom)
    resp = client.post("/api/ai/coach", json={"week_summary": {}}, headers=CLIENT_HEADERS)
    assert resp.status_code == 500
    assert "sk-live-123" not in resp.text


def test_compliance_plan_failure_is_a_clean_500(client, monkeypatch):
    training_routes._PLANNED_WORKOUT_CACHE.clear()

    def boom(_d):
        raise RuntimeError("feed key abc123")

    monkeypatch.setattr(training_routes, "get_planned_workout_for_date", boom)
    resp = client.get("/api/compliance", params={"target_date": "2026-08-10"})
    assert resp.status_code == 500
    assert "abc123" not in resp.text


def test_compliance_bad_date_is_400(client):
    resp = client.get("/api/compliance", params={"target_date": "10/08/2026"})
    assert resp.status_code == 400


def test_gear_failure_is_a_clean_500(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db locked")

    monkeypatch.setattr("src.analytics.gear.extract_gear_summary", boom)
    monkeypatch.setattr(training_routes, "load_cache", lambda: {"activities": [make_activity(1, "2026-08-03")], "details": {}})
    resp = client.get("/api/gear")
    assert resp.status_code == 500
    assert "db locked" not in resp.text


def test_sync_failure_is_a_clean_500(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("token secret xyz789")

    monkeypatch.setattr(strava_routes, "get_access_token", boom)
    resp = client.post("/api/sync", json={}, headers=CLIENT_HEADERS)
    assert resp.status_code == 500
    assert "xyz789" not in resp.text


SECRET_MARKER = "secret-token-xyz789"


def _boom(*_a, **_k):
    raise RuntimeError(SECRET_MARKER)


@pytest.mark.parametrize(
    ("target", "method", "path", "body"),
    [
        ("src.api.routes.dashboard.get_weather_outlook", "get", "/api/weather", None),
        ("src.api.routes.assistant.coach_chat", "post", "/api/ai/chat", {"message": "hi"}),
        ("src.api.routes.assistant.list_remembered_facts", "get", "/api/coach/memory", None),
        ("src.api.routes.assistant.forget_remembered_fact", "delete", "/api/coach/memory/abc", None),
        ("src.api.routes.assistant.extract_session_facts", "post", "/api/coach/memory/extract", {"session_id": "s1"}),
        ("src.integrations.garmin_workouts.preview_week_workouts", "get", "/api/workouts/preview", None),
        ("src.integrations.garmin_workouts.sync_week_workouts_to_garmin", "post", "/api/workouts/sync", {}),
    ],
)
def test_endpoint_failures_are_clean_500s(client, monkeypatch, target, method, path, body):
    monkeypatch.setattr(target, _boom)
    kwargs = {"headers": CLIENT_HEADERS}
    if body is not None:
        kwargs["json"] = body
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 500
    assert SECRET_MARKER not in resp.text
    assert resp.json()["detail"].endswith("failed.")


def test_backfill_failure_is_a_clean_500(client, monkeypatch):
    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(strava_routes, "get_access_token", lambda interactive=False: "tok")
    monkeypatch.setattr(strava_routes, "init_db", _Conn)
    monkeypatch.setattr(strava_routes, "backfill_all", _boom)
    resp = client.post("/api/history/backfill", json={}, headers=CLIENT_HEADERS)
    assert resp.status_code == 500
    assert SECRET_MARKER not in resp.text


def test_history_status_failure_is_a_clean_500(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(strava_routes, "init_db", boom)
    resp = client.get("/api/history/status")
    assert resp.status_code == 500
    assert "disk full" not in resp.text


def test_weekly_compliance_endpoint_returns_week_and_trend(client, monkeypatch):
    from datetime import date

    monkeypatch.setattr(training_routes, "today_local", lambda: date(2026, 8, 12))
    monkeypatch.setattr(
        training_routes, "get_plan_texts_between",
        lambda start, end: {date(2026, 8, 10): "Τρέξιμο 10 χλμ"},
    )
    monkeypatch.setattr(training_routes, "read_history", lambda count: ([make_activity(1, "2026-08-10")], {}))
    monkeypatch.setattr(training_routes, "load_cache", lambda: {"activities": [], "details": {}})

    resp = client.get("/api/compliance/weekly", params={"weeks": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert [t["week_monday"] for t in body["trend"]] == ["2026-07-27", "2026-08-03", "2026-08-10"]
    assert body["week"]["week_monday"] == "2026-08-10"
    assert body["week"]["actual_run_km"] == 10.0
    assert body["week"]["weekly_score"] == 100


def test_weekly_compliance_plan_failure_is_a_clean_500(client, monkeypatch):
    def boom(*_a):
        raise RuntimeError("secret feed url")

    monkeypatch.setattr(training_routes, "get_plan_texts_between", boom)
    resp = client.get("/api/compliance/weekly")
    assert resp.status_code == 500
    assert "secret" not in resp.text


def test_strava_webhook_worker_passes_token_then_activity_id(monkeypatch):
    import src.api.routes.strava as strava_routes

    calls = []
    monkeypatch.setattr(strava_routes, "get_access_token", lambda interactive=False: "tok")
    monkeypatch.setattr(strava_routes, "fetch_activity_detail", lambda t, i: calls.append((t, i)) or {})
    strava_routes._process_strava_webhook_activity(12345)
    assert calls == [("tok", 12345)]


def test_decoupling_endpoint_merges_cached_and_stored_details(client, monkeypatch):
    splits = [{"distance": 1000.0, "moving_time": 300, "average_heartrate": 140} for _ in range(14)]
    acts = [
        {"id": 1, "sport_type": "Run", "moving_time": 4200, "distance": 14000, "start_date_local": "2026-11-08T08:00:00"},
        {"id": 2, "sport_type": "Run", "moving_time": 4200, "distance": 14000, "start_date_local": "2026-11-15T08:00:00"},
    ]
    monkeypatch.setattr(dashboard_routes, "get_summaries", lambda count: (acts, {"2": {"splits_metric": splits}}))
    monkeypatch.setattr(dashboard_routes, "read_history", lambda count: ([], {"1": {"splits_metric": splits}}))
    monkeypatch.setattr(dashboard_routes, "read_fit_runs", lambda: [])
    body = client.get("/api/decoupling").json()
    assert [r["id"] for r in body["runs"]] == [1, 2]
    assert body["recent_status"] == "good"


def test_decoupling_endpoint_hides_internal_errors(client, monkeypatch):
    def boom(count):
        raise RuntimeError("secret path /x")

    monkeypatch.setattr(dashboard_routes, "get_summaries", boom)
    resp = client.get("/api/decoupling")
    assert resp.status_code == 500
    assert "secret" not in resp.text


def test_decoupling_endpoint_includes_fit_runs_and_limits(client, monkeypatch):
    splits = [{"distance": 1000.0, "moving_time": 300, "average_heartrate": 140} for _ in range(14)]
    fit = [
        {"start_utc": f"2026-0{m}-01T07:00:00Z", "start_local": f"2026-0{m}-01T09:00:00",
         "distance_m": 14000.0, "moving_time": 4200, "splits": splits}
        for m in (3, 4)
    ]
    monkeypatch.setattr(dashboard_routes, "get_summaries", lambda count: ([], {}))
    monkeypatch.setattr(dashboard_routes, "read_history", lambda count: ([], {}))
    monkeypatch.setattr(dashboard_routes, "read_fit_runs", lambda: fit)
    body = client.get("/api/decoupling").json()
    assert body["fit_runs"] == 2 and [r["source"] for r in body["runs"]] == ["fit", "fit"]
    assert [r["date"] for r in client.get("/api/decoupling?runs=1").json()["runs"]] == ["2026-04-01"]
    assert client.get("/api/decoupling?runs=0").status_code == 422


# --- /api/sport-trends ---------------------------------------------------------


def test_sport_trends_endpoint_merges_strava_and_fit(client, monkeypatch):
    acts = [make_activity(1, "2026-10-05", "Ride", start_date="2026-10-05T05:30:00Z")]
    fit = [{"start_utc": "2025-10-05T05:30:00Z", "start_local": "2025-10-05T08:30:00", "sport": "swimming",
            "distance_m": 2000.0, "moving_time": 2400, "avg_hr": None, "avg_power": None}]
    monkeypatch.setattr(dashboard_routes, "read_all_summaries", lambda limit: acts)
    monkeypatch.setattr(dashboard_routes, "read_fit_activities", lambda: fit)
    monkeypatch.setattr(dashboard_routes, "today_local", lambda: __import__("datetime").date(2026, 11, 21))
    body = client.get("/api/sport-trends?bucket=quarter").json()
    assert body["periods"][0] == "2025-Q4" and body["periods"][-1] == "2026-Q4"
    assert body["sources"] == {"strava": 1, "fit": 1}
    assert body["sports"]["swim"]["series"][0]["efficiency"] == 120.0


def test_sport_trends_endpoint_rejects_bad_bucket_and_hides_errors(client, monkeypatch):
    assert client.get("/api/sport-trends?bucket=week").status_code == 422

    def boom(limit):
        raise RuntimeError("secret path /x")

    monkeypatch.setattr(dashboard_routes, "read_all_summaries", boom)
    resp = client.get("/api/sport-trends")
    assert resp.status_code == 500 and "secret" not in resp.text


def test_read_all_summaries_prefers_cache_and_survives_store_failure(monkeypatch, tmp_path):
    from src.api import activity_data
    from src.storage.activity_store import init_db, upsert_activities

    db = tmp_path / "h.db"
    conn = init_db(str(db))
    upsert_activities(conn, [make_activity(1, "2026-10-01", name="stored"), make_activity(2, "2026-10-02")])
    conn.close()
    monkeypatch.setattr(activity_data, "init_db", lambda: init_db(str(db)))
    monkeypatch.setattr(activity_data, "load_cache", lambda: {"activities": [make_activity(1, "2026-10-01", name="cached")]})
    merged = {a["id"]: a["name"] for a in activity_data.read_all_summaries(100)}
    assert merged == {1: "cached", 2: "Run 2"}

    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(activity_data, "init_db", broken)
    assert [a["name"] for a in activity_data.read_all_summaries(100)] == ["cached"]
