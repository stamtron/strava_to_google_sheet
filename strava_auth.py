"""
Strava OAuth2 Authentication Module.

Handles the full OAuth2 lifecycle:
- Opens browser for user authorization
- Captures callback via local HTTP server
- Exchanges authorization code for tokens
- Caches tokens in token.json
- Refreshes expired tokens automatically
"""

import json
import os
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/callback"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

    authorization_code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            _CallbackHandler.authorization_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization successful!</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            error = query.get("error", ["unknown"])[0]
            self.wfile.write(
                f"<html><body><h2>Authorization failed: {error}</h2></body></html>".encode()
            )

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass


def _save_token(token_data: dict) -> None:
    """Save token data to token.json."""
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"✅ Token saved to {TOKEN_FILE}")


def _load_token() -> dict | None:
    """Load token data from token.json if it exists."""
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)


def _refresh_token(refresh_token: str) -> dict:
    """Use refresh token to get a new access token."""
    print("🔄 Refreshing access token...")
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    response.raise_for_status()
    token_data = response.json()
    _save_token(token_data)
    return token_data


def _authorize() -> dict:
    """Run the full OAuth2 authorization flow."""
    if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
        raise ValueError(
            "Missing STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET in .env file.\n"
            "Go to https://www.strava.com/settings/api to create an app."
        )

    # Build authorization URL
    auth_params = (
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&approval_prompt=auto"
        f"&scope=activity:read_all"
    )
    auth_url = AUTH_URL + auth_params

    print("🌐 Opening browser for Strava authorization...")
    print(f"   If it doesn't open, go to:\n   {auth_url}\n")
    webbrowser.open(auth_url)

    # Start local server to capture the callback
    server = HTTPServer(("localhost", 8000), _CallbackHandler)
    print("⏳ Waiting for authorization callback on http://localhost:8000 ...")
    server.handle_request()  # Handle exactly one request
    server.server_close()

    code = _CallbackHandler.authorization_code
    if not code:
        raise RuntimeError("Failed to receive authorization code from Strava.")

    print("🔑 Exchanging authorization code for tokens...")

    # Exchange code for tokens
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    token_data = response.json()
    _save_token(token_data)
    return token_data


def get_access_token() -> str:
    """
    Get a valid Strava access token.

    - If a cached token exists and is still valid, returns it.
    - If it's expired, refreshes it automatically.
    - If no token exists, initiates the full OAuth2 authorization flow.

    Returns:
        A valid access token string.
    """
    token_data = _load_token()

    if token_data:
        expires_at = token_data.get("expires_at", 0)
        if time.time() < expires_at:
            print("✅ Using cached access token (still valid)")
            return token_data["access_token"]
        else:
            token_data = _refresh_token(token_data["refresh_token"])
            return token_data["access_token"]
    else:
        token_data = _authorize()
        return token_data["access_token"]
