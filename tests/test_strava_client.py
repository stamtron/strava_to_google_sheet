"""Error-path tests for the Strava client: rate limiting, retries, connectivity."""

import pytest
import requests

from src import config
from src.integrations import strava
from src.integrations.strava import StravaNetworkError, StravaRateLimitError


class _Response:
    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


def test_network_failure_becomes_a_strava_network_error(monkeypatch):
    """A proxy/DNS/TLS failure must not escape as a raw requests traceback."""
    def boom(*args, **kwargs):
        raise requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden")

    monkeypatch.setattr(strava.requests, "request", boom)
    with pytest.raises(StravaNetworkError) as exc:
        strava._request("GET", strava.ACTIVITIES_URL)
    assert "Could not reach Strava" in str(exc.value)


def test_timeout_becomes_a_strava_network_error(monkeypatch):
    monkeypatch.setattr(
        strava.requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("timed out"))
    )
    with pytest.raises(StravaNetworkError):
        strava._request("GET", strava.ACTIVITIES_URL)


def test_successful_response_is_returned_unchanged(monkeypatch):
    ok = _Response(200)
    monkeypatch.setattr(strava.requests, "request", lambda *a, **k: ok)
    assert strava._request("GET", strava.ACTIVITIES_URL) is ok


def test_non_429_errors_are_not_retried(monkeypatch):
    """A 404 is the caller's problem to interpret, not something to retry."""
    calls = []
    monkeypatch.setattr(
        strava.requests, "request", lambda *a, **k: (calls.append(1), _Response(404))[1]
    )
    assert strava._request("GET", strava.ACTIVITIES_URL).status_code == 404
    assert len(calls) == 1


def test_rate_limit_is_retried_then_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(
        strava.requests,
        "request",
        lambda *a, **k: (calls.append(1), _Response(429, {"X-RateLimit-Usage": "201,1200"}))[1],
    )
    monkeypatch.setattr(strava.time, "sleep", lambda _: None)

    with pytest.raises(StravaRateLimitError) as exc:
        strava._request("GET", strava.ACTIVITIES_URL)

    assert len(calls) == config.STRAVA_MAX_RETRIES
    assert "201,1200" in str(exc.value)


def test_rate_limit_recovers_when_a_retry_succeeds(monkeypatch):
    responses = [_Response(429), _Response(200)]
    monkeypatch.setattr(strava.requests, "request", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(strava.time, "sleep", lambda _: None)
    assert strava._request("GET", strava.ACTIVITIES_URL).status_code == 200


def test_retry_after_header_is_honoured_but_capped(monkeypatch):
    """Never sleep out a full 15-minute window inside a request thread."""
    slept = []
    monkeypatch.setattr(
        strava.requests, "request", lambda *a, **k: _Response(429, {"Retry-After": "900"})
    )
    monkeypatch.setattr(strava.time, "sleep", slept.append)

    with pytest.raises(StravaRateLimitError):
        strava._request("GET", strava.ACTIVITIES_URL)

    assert slept and max(slept) <= 30.0


def test_malformed_retry_after_falls_back_to_backoff(monkeypatch):
    slept = []
    monkeypatch.setattr(
        strava.requests, "request", lambda *a, **k: _Response(429, {"Retry-After": "soon"})
    )
    monkeypatch.setattr(strava.time, "sleep", slept.append)

    with pytest.raises(StravaRateLimitError):
        strava._request("GET", strava.ACTIVITIES_URL)

    assert all(s > 0 for s in slept)


def test_detail_fetch_keeps_partial_results_on_network_loss(monkeypatch):
    """Activities already fetched must survive a mid-loop connectivity drop."""
    activities = [{"id": 1}, {"id": 2}, {"id": 3}]

    def flaky(_token, act_id):
        if act_id == 2:
            raise StravaNetworkError("Could not reach Strava")
        return {"calories": 100 * act_id}

    monkeypatch.setattr(strava, "fetch_activity_detail", flaky)
    details = strava.fetch_details_for_activities("tok", activities, delay_sec=0)

    assert details == {"1": {"calories": 100}}


def test_detail_fetch_skips_already_cached_details(monkeypatch):
    """Cached details are the main rate-limit defence — never re-fetch them."""
    fetched = []

    def counting(_token, act_id):
        fetched.append(act_id)
        return {"calories": 1}

    monkeypatch.setattr(strava, "fetch_activity_detail", counting)
    details = strava.fetch_details_for_activities(
        "tok", [{"id": 1}, {"id": 2}], known_details={1: {"calories": 999}}, delay_sec=0
    )

    assert fetched == [2]
    assert details["1"] == {"calories": 999}


def test_detail_keys_are_strings_for_json_round_trips(monkeypatch):
    monkeypatch.setattr(strava, "fetch_activity_detail", lambda _t, _i: {"calories": 1})
    details = strava.fetch_details_for_activities("tok", [{"id": 42}], delay_sec=0)
    assert list(details) == ["42"]


def test_activities_without_ids_are_skipped(monkeypatch):
    monkeypatch.setattr(strava, "fetch_activity_detail", lambda _t, _i: {"calories": 1})
    assert strava.fetch_details_for_activities("tok", [{}], delay_sec=0) == {}


def test_oauth_callback_port_differs_from_the_dashboard_port():
    """Sharing the port deadlocks the OAuth flow against the running server."""
    assert config.STRAVA_REDIRECT_PORT != config.SERVER_PORT
    assert str(config.STRAVA_REDIRECT_PORT) in strava.REDIRECT_URI
