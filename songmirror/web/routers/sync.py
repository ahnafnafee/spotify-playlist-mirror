"""Sync control: run now, schedule, status."""

import asyncio

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/sync/run")
async def run(request: Request, execute: bool = False):
    # The dashboard's global "Sync now": run every enabled job in turn, serialized
    # on SyncService's lock. Fire-and-forget so the UI stays responsive.
    asyncio.create_task(request.app.state.sync.run_all(execute=execute))
    return JSONResponse({"queued": True}, status_code=202)


@router.get("/api/sync/status")
def status(request: Request):
    return request.app.state.sync.status()


@router.post("/api/sync/schedule")
async def schedule(request: Request, body: dict = Body(default={})):
    sync = request.app.state.sync
    if body.get("interval"):
        request.app.state.settings.save({"SYNC_INTERVAL": body["interval"]})
    # Persist the on/off choice so it survives restarts — the scheduler reads
    # AUTO_SYNC on boot (see SyncService.start).
    # The global master switch: pause/resume gates every job's scheduler.
    # reconcile() reads AUTO_SYNC and starts/cancels per-job timers accordingly.
    action = body.get("action")
    if action == "pause":
        request.app.state.settings.save({"AUTO_SYNC": "off"})
        await sync.reconcile()
    elif action == "resume":
        request.app.state.settings.save({"AUTO_SYNC": "on"})
        await sync.reconcile()
    return sync.status()
