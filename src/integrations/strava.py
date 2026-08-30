"""
Strava API Client and OAuth2 Authentication.
"""

import json
import os
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests
from src.config import (
    STRAVA_CLIENT_ID,
    STRAVA_CLIENT_SECRET,
    STRAVA_MAX_RETRIES,
    STRAVA_REDIRECT_PORT,
    TOKEN_FILE,
)

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
ACTIVITY_DETAIL_URL = "https://www.strava.com/api/v3/activities"
REDIRECT_URI = f"http://localhost:{STRAVA_REDIRECT_PORT}/callback"
SCOPES = "read,activity:read_all"


class StravaAuthRequired(Exception):
    """Raised when interactive re-authorization is needed but not permitted."""


class StravaRateLimitError(Exception):
    """Raised when Strava's rate limit is hit and retries are exhausted."""


class StravaNetworkError(Exception):
    """Raised when Strava is unreachable (DNS, proxy, TLS, or timeout)."""


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            _OAuthCallbackHandler.auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authorization successful!</h1><p>You can close this tab and return to the terminal.</p>"
            )
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization failed!</h1><p>No code received.</p>")

    def log_message(self, format, *args):
        pass


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """
    Perform a Strava API request, retrying with backoff when rate limited.

    Raises StravaRateLimitError once retries are exhausted rather than returning
    an empty result, so callers never mistake throttling for "no data".
    Connectivity failures become StravaNetworkError so callers can report them
    instead of surfacing a urllib3 traceback.
    """
    last_response = None
    for attempt in range(STRAVA_MAX_RETRIES):
        try:
            response = requests.request(method, url, timeout=30, **kwargs)
        except requests.RequestException as e:
            raise StravaNetworkError(f"Could not reach Strava ({url}): {e}") from e
        if response.status_code != 429:
            return response

        last_response = response
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else 2.0 * (2**attempt)
        except ValueError:
            delay = 2.0 * (2**attempt)
        # Capped: a 15-minute window reset must not hang a request thread.
        delay = min(delay, 30.0)
        if attempt < STRAVA_MAX_RETRIES - 1:
            print(f"  ⏳ Strava rate limit hit, retrying in {delay:.0f}s...")
            time.sleep(delay)

    usage = last_response.headers.get("X-RateLimit-Usage", "unknown") if last_response else "unknown"
    raise StravaRateLimitError(
        f"Strava rate limit exceeded after {STRAVA_MAX_RETRIES} attempts (usage: {usage}). "
        "Strava allows 200 requests per 15 minutes; wait for the window to reset."
    )


def _load_cached_token() -> dict | None:
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_token(token_data: dict) -> None:
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)


def _refresh_access_token(refresh_token: str) -> dict:
    payload = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    token_data = response.json()
    _save_token(token_data)
    return token_data


def _authorize_new_user() -> dict:
    if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
        raise ValueError("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env")

    params = {
        "client_id": STRAVA_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "approval_prompt": "auto",
        "scope": SCOPES,
    }
    req = requests.Request("GET", AUTH_URL, params=params).prepare()
    print(f"\nOpening browser for Strava authorization...\n  URL: {req.url}\n")
    webbrowser.open(req.url)

    _OAuthCallbackHandler.auth_code = None
    try:
        server = HTTPServer(("localhost", STRAVA_REDIRECT_PORT), _OAuthCallbackHandler)
    except OSError as e:
        raise StravaAuthRequired(
            f"Cannot bind OAuth callback port {STRAVA_REDIRECT_PORT}: {e}. Set "
            "STRAVA_REDIRECT_PORT in .env to a free port, and register it as an "
            "Authorized Callback Domain for your Strava app."
        ) from e

    try:
        while _OAuthCallbackHandler.auth_code is None:
            server.handle_request()
    finally:
        server.server_close()

    payload = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": _OAuthCallbackHandler.auth_code,
        "grant_type": "authorization_code",
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    token_data = response.json()
    _save_token(token_data)
    return token_data


def get_access_token(interactive: bool = True) -> str:
    """
    Get a valid Strava access token, refreshing or authenticating as needed.

    With interactive=False (the web server) a missing or unrefreshable token
    raises StravaAuthRequired instead of opening a browser and blocking the
    calling thread on a redirect that will never arrive.
    """
    cached = _load_cached_token()
    if cached:
        expires_at = cached.get("expires_at", 0)
        if time.time() < (expires_at - 60):
            return cached["access_token"]
        if "refresh_token" in cached:
            try:
                return _refresh_access_token(cached["refresh_token"])["access_token"]
            except requests.RequestException as e:
                if not interactive:
                    raise StravaAuthRequired(
                        f"Strava token refresh failed ({e}). Run 'python main.py' in a "
                        "terminal to re-authorize."
                    ) from e

    if not interactive:
        raise StravaAuthRequired(
            "No valid Strava token available. Run 'python main.py' in a terminal to authorize."
        )

    return _authorize_new_user()["access_token"]


def fetch_activities(access_token: str, per_page: int = 30, page: int = 1) -> list[dict]:
    """Fetch athlete activities from Strava."""
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": per_page, "page": page}
    response = _request("GET", ACTIVITIES_URL, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def fetch_activity_detail(access_token: str, activity_id: int) -> dict:
    """
    Fetch full details for an activity (HR, temperature, calories, suffer score).

    Returns {} only when the activity genuinely has no detail available.
    Throttling raises StravaRateLimitError, so partial data is never written to
    the sheet as though it were complete.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    response = _request("GET", f"{ACTIVITY_DETAIL_URL}/{activity_id}", headers=headers)
    if response.status_code == 200:
        return response.json()
    if response.status_code in (401, 403):
        raise StravaAuthRequired(
            f"Strava rejected the request for activity {activity_id} (HTTP "
            f"{response.status_code}). The token may lack the activity:read_all scope."
        )
    return {}


def fetch_details_for_activities(
    access_token: str,
    activities: list[dict],
    known_details: dict | None = None,
    delay_sec: float = 0.0,
    progress: bool = False,
) -> dict:
    """
    Fetch details for activities, reusing any already-known details.

    Skipping activities whose details are already cached is the main defence
    against Strava's rate limit — re-syncing the same window costs no extra
    calls. Keys are stringified activity IDs so the result survives a JSON
    round-trip unchanged.
    """
    details = {str(k): v for k, v in (known_details or {}).items()}

    for act in activities:
        act_id = act.get("id")
        if act_id is None:
            continue
        key = str(act_id)
        if details.get(key):
            continue
        try:
            details[key] = fetch_activity_detail(access_token, act_id)
        except StravaRateLimitError:
            if progress:
                print("\n  ⚠️  Stopped fetching details early: Strava rate limit reached.")
            break
        except StravaNetworkError as e:
            # Details are supplementary; keep what we have rather than losing the
            # activities we already fetched successfully.
            if progress:
                print(f"\n  ⚠️  Stopped fetching details early: {e}")
            break
        if progress:
            print(".", end="", flush=True)
        if delay_sec > 0:
            time.sleep(delay_sec)

    return details
