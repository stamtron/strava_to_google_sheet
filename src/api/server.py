"""
FastAPI application for the StaminAI training dashboard.

Builds the app (lifespan, CORS, client-header guard), mounts the feature routers
from `src/api/routes/`, and serves the static frontend. Endpoints live in the
routers; shared helpers live in `deps.py` and `activity_data.py`.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.deps import verify_client
from src.api.routes import assistant, dashboard, notifications, strava, training
from src.config import ALLOWED_ORIGINS, WEB_DIR

server_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager running background Telegram bot poller and auto-dispatcher."""
    async def telegram_worker():
        from src.integrations.telegram import (
            get_telegram_webhook_info,
            poll_telegram_updates,
            process_incoming_update,
            run_daily_dispatch_check,
        )

        offset = 0
        server_logger.info("Starting Telegram bot background worker and daily scheduler...")

        webhook_info = await asyncio.to_thread(get_telegram_webhook_info)
        webhook_active = bool(webhook_info and webhook_info.get("url"))
        if webhook_active:
            server_logger.info(
                "Telegram webhook is active (%s); worker will run daily scheduler only (no getUpdates).",
                webhook_info.get("url"),
            )

        while True:
            try:
                # 1. Check if it is time to dispatch daily workout brief
                await asyncio.to_thread(run_daily_dispatch_check)

                # 2. Poll for incoming athlete commands from Telegram only if no webhook is set
                if not webhook_active:
                    offset, updates = await asyncio.to_thread(poll_telegram_updates, offset=offset, timeout=10)
                    for u in updates:
                        await asyncio.to_thread(process_incoming_update, u)
                    await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                server_logger.warning("Telegram worker error: %s", e)
                await asyncio.sleep(5)

    task = asyncio.create_task(telegram_worker())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="StaminAI Training API",
    version="2.0.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_client)],
)

# Explicit origins, credentials off: the API is unauthenticated, so a wildcard
# origin with credentials would let any page the browser visits read this data.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

for _module in (dashboard, strava, assistant, training, notifications):
    app.include_router(_module.router)


# Serve Static UI
if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def serve_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Web app frontend building in progress..."}


@app.get("/favicon.ico")
def serve_favicon():
    favicon_file = os.path.join(WEB_DIR, "favicon.svg")
    if os.path.exists(favicon_file):
        return FileResponse(favicon_file, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")
