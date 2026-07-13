"""Sync settings: read (secrets masked, env-backed) / update."""

import os

from fastapi import APIRouter, Body, Request

from ...services.accounts import CONNECTORS

router = APIRouter()

# Never echo secret credentials back to the browser.
SECRET_KEYS = {f.key for cls in CONNECTORS.values() for f in cls.config_fields if f.secret}

# Non-secret config the UI manages. When settings.json doesn't have a key, fall
# back to the process environment — a docker-compose env_file / .env (the user's
# gitignored config) — so the form reflects the actual running values, not blanks.
CONFIG_KEYS = ("DISPLAY_NAME", "SYNC_MODE", "SYNC_SOURCE", "SYNC_INTERVAL", "PROVIDERS", "MAX_ADDS",
               "MAX_REMOVALS", "PLAYLISTS", "DOWNLOAD_DIR", "LOCAL_MIRROR_FORMAT")


@router.get("/api/settings")
def get_settings(request: Request):
    out = {k: v for k, v in request.app.state.settings.load().items() if k not in SECRET_KEYS}
    for key in CONFIG_KEYS:
        if key not in out and os.getenv(key):
            out[key] = os.getenv(key)
    return out


@router.put("/api/settings")
def put_settings(request: Request, values: dict = Body(...)):
    request.app.state.settings.save(values)
    return {"ok": True}
