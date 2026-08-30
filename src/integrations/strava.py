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
from src.config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, TOKEN_FILE

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
ACTIVITY_DETAIL_URL = "https://www.strava.com/api/v3/activities"
REDIRECT_PORT = 8000
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "read,activity:read_all"


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
    response = requests.post(TOKEN_URL, data=payload)
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

    server = HTTPServer(("localhost", REDIRECT_PORT), _OAuthCallbackHandler)
    while _OAuthCallbackHandler.auth_code is None:
        server.handle_request()
    server.server_close()

    code = _OAuthCallbackHandler.auth_code
    payload = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }
    response = requests.post(TOKEN_URL, data=payload)
    response.raise_for_status()
    token_data = response.json()
    _save_token(token_data)
    return token_data


def get_access_token() -> str:
    """Get a valid Strava access token, refreshing or authenticating as needed."""
    cached = _load_cached_token()
    if cached:
        expires_at = cached.get("expires_at", 0)
        if time.time() < (expires_at - 60):
            return cached["access_token"]
        if "refresh_token" in cached:
            try:
                new_token = _refresh_access_token(cached["refresh_token"])
                return new_token["access_token"]
            except requests.RequestException:
                pass
    token_data = _authorize_new_user()
    return token_data["access_token"]


def fetch_activities(access_token: str, per_page: int = 30, page: int = 1) -> list[dict]:
    """Fetch athlete activities from Strava."""
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"per_page": per_page, "page": page}
    response = requests.get(ACTIVITIES_URL, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def fetch_activity_detail(access_token: str, activity_id: int) -> dict:
    """Fetch full details for a specific activity (HR, temperature, calories, suffer score)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(f"{ACTIVITY_DETAIL_URL}/{activity_id}", headers=headers)
    if response.status_code == 200:
        return response.json()
    return {}
