"""FastAPI application for the web GUI (Phase 1).

Thin HTTP/SSE layer over the platform services (settings, events, sync,
accounts). Drives services, which drive the engine — it never reaches into the
engine directly.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..services.events import EventBus
from ..services.playlists import LinkStore
from ..services.settings import SettingsStore
from ..services.sync_service import SyncService
from ..services.syncs import SyncStore
from ..services.transfers import TransferService
from .routers import (
    accounts, events, playlists, settings as settings_router, sync,
    syncs as syncs_router, transfers as transfers_router,
)

# Built React SPA (Vite output), served in production; in dev the vite server
# proxies /api and /events to this app instead.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(settings=None, bus=None, sync_service=None, links=None, transfers=None, syncs=None) -> FastAPI:
    settings = settings or SettingsStore()
    bus = bus or EventBus()
    syncs = syncs or SyncStore(dir=Path(settings.env_path).parent)
    sync_service = sync_service or SyncService(settings, bus, syncs)
    links = links or LinkStore(dir=Path(settings.env_path).parent)
    transfers = transfers or TransferService(settings, bus, sync_service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        bus.attach_to_logs()
        # Surface the gitignored config (.env / docker env_file) to os.getenv so
        # the settings API shows the actual running defaults; the managed env
        # file + settings.json then take precedence.
        load_dotenv()
        os.environ["OMNI_ENV_FILE"] = settings.env_path
        settings.apply_to_env()
        await sync_service.start()
        try:
            yield
        finally:
            await sync_service.shutdown()

    app = FastAPI(title="Omni Playlist Sync", lifespan=lifespan)
    app.state.settings = settings
    app.state.bus = bus
    app.state.sync = sync_service
    app.state.syncs = syncs
    app.state.links = links
    app.state.transfers = transfers

    app.include_router(accounts.router)
    app.include_router(settings_router.router)
    app.include_router(sync.router)
    app.include_router(syncs_router.router)
    app.include_router(events.router)
    app.include_router(playlists.router)
    app.include_router(transfers_router.router)

    @app.get("/health")
    def health():
        return {"ok": True}

    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Serve the SPA shell for any non-API path (client-side routing).
        if full_path.startswith(("api/", "events", "oauth/")):
            return JSONResponse({"detail": "not found"}, status_code=404)
        # Serve real files sitting at the dist root (favicons, etc.) before the SPA
        # shell. The containment check blocks path traversal out of the dist dir.
        if full_path:
            candidate = (_DIST / full_path).resolve()
            if candidate.is_file() and _DIST.resolve() in candidate.parents:
                return FileResponse(str(candidate))
        index = _DIST / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return JSONResponse(
            {"detail": "frontend not built — run: pnpm -C frontend install && pnpm -C frontend build"},
            status_code=503,
        )

    return app


app = create_app()
