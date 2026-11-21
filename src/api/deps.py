"""Request guards and error helpers shared by every API router."""

import hmac
import logging

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Webhooks are called by Strava and Telegram, which cannot send our client
# header; they authenticate with their own shared secrets instead.
_CLIENT_CHECK_EXEMPT_PATHS = frozenset({
    "/api/strava/webhook",
    "/api/notifications/telegram/webhook",
})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def internal_error(action: str) -> HTTPException:
    """
    Log the active exception and build a generic 500 for `action`.

    Call inside an `except` block. Exception text can carry tokens, feed URLs or
    file paths, so it goes to the log and the client only sees which action failed.
    """
    logger.exception("%s failed", action)
    return HTTPException(status_code=500, detail=f"{action} failed.")


def verify_client(request: Request) -> None:
    """
    Guard every state-changing /api route against cross-site requests.

    A custom header cannot be sent cross-origin without a CORS preflight, which
    ALLOWED_ORIGINS refuses, so requiring it blocks CSRF from any page the
    browser visits. When API_AUTH_TOKEN is set, a bearer token is also required
    so other machines on the network cannot trigger syncs or Telegram sends.
    """
    path = request.url.path
    if request.method in _SAFE_METHODS or not path.startswith("/api/") or path in _CLIENT_CHECK_EXEMPT_PATHS:
        return

    # Read at call time so tests (and a reloaded .env) can change them.
    from src import config

    if request.headers.get(config.API_CLIENT_HEADER) != config.API_CLIENT_HEADER_VALUE:
        raise HTTPException(status_code=403, detail=f"Missing or invalid {config.API_CLIENT_HEADER} header.")

    if config.API_AUTH_TOKEN:
        auth = request.headers.get("Authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), config.API_AUTH_TOKEN):
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
