"""FastAPI application for the web GUI (Phase 1).

Thin HTTP/SSE layer over the platform services (settings, events, sync,
accounts). Drives services, which drive the engine — it never reaches into the
engine directly.
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..events import EventBus
from ..settings import SettingsStore
from ..sync_service import SyncService
from .routers import accounts, events, settings as settings_router, sync


def create_app(settings=None, bus=None, sync_service=None) -> FastAPI:
    settings = settings or SettingsStore()
    bus = bus or EventBus()
    sync_service = sync_service or SyncService(settings, bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        bus.attach_to_logs()
        # Point the engine at the managed env file and apply current settings so
        # wizard-saved values win over any stale .env.
        os.environ["OMNI_ENV_FILE"] = settings.env_path
        settings.apply_to_env()
        await sync_service.start()
        try:
            yield
        finally:
            await sync_service.stop()

    app = FastAPI(title="Omni Playlist Sync", lifespan=lifespan)
    app.state.settings = settings
    app.state.bus = bus
    app.state.sync = sync_service

    app.include_router(accounts.router)
    app.include_router(settings_router.router)
    app.include_router(sync.router)
    app.include_router(events.router)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()
