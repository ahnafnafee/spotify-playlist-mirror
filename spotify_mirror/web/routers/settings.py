"""Sync settings: read (secrets masked) / update."""

from fastapi import APIRouter, Body, Request

from ...accounts import CONNECTORS

router = APIRouter()

# Never echo secret credentials back to the browser.
SECRET_KEYS = {f.key for cls in CONNECTORS.values() for f in cls.config_fields if f.secret}


@router.get("/api/settings")
def get_settings(request: Request):
    return {k: v for k, v in request.app.state.settings.load().items() if k not in SECRET_KEYS}


@router.put("/api/settings")
def put_settings(request: Request, values: dict = Body(...)):
    request.app.state.settings.save(values)
    return {"ok": True}
