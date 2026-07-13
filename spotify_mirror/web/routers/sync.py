"""Sync control: run now, schedule, status."""

import asyncio

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/sync/run")
async def run(request: Request, execute: bool = False):
    # Fire-and-forget onto SyncService's single queue; a pass already running
    # coalesces. Returns immediately so the UI stays responsive.
    asyncio.create_task(request.app.state.sync.run_now(execute=execute))
    return JSONResponse({"queued": True}, status_code=202)


@router.get("/api/sync/status")
def status(request: Request):
    return request.app.state.sync.status()


@router.post("/api/sync/schedule")
async def schedule(request: Request, body: dict = Body(default={})):
    sync = request.app.state.sync
    if body.get("interval"):
        request.app.state.settings.save({"SYNC_INTERVAL": body["interval"]})
    action = body.get("action")
    if action == "pause":
        await sync.stop()
    elif action == "resume":
        await sync.start()
    return sync.status()
