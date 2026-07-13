"""Named sync jobs: list / create / update / delete / run-now.

The scheduler (SyncService) is reconciled after every mutation so per-job timers
stay in step with the store.
"""

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ...services.syncs import SyncJob

router = APIRouter()

_FIELDS = {"name", "enabled", "mode", "source", "providers", "playlists",
           "interval", "max_adds", "max_removals", "download", "id"}


def _job_from(values):
    """SyncJob from a request dict — unknown keys dropped, types coerced."""
    data = {k: v for k, v in values.items() if k in _FIELDS}
    for k in ("max_adds", "max_removals"):
        if k in data:
            data[k] = int(data[k])
    for k in ("enabled", "download"):
        if k in data:
            data[k] = bool(data[k])
    return SyncJob(**data)


@router.get("/api/syncs")
def list_syncs(request: Request):
    return [asdict(j) for j in request.app.state.syncs.list()]


@router.post("/api/syncs")
async def create_sync(request: Request, values: dict = Body(...)):
    job = request.app.state.syncs.upsert(_job_from(values))
    await request.app.state.sync.reconcile()
    return asdict(job)


@router.put("/api/syncs/{job_id}")
async def update_sync(job_id: str, request: Request, values: dict = Body(...)):
    existing = request.app.state.syncs.get(job_id)
    if existing is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    job = request.app.state.syncs.upsert(_job_from({**asdict(existing), **values, "id": job_id}))
    await request.app.state.sync.reconcile()
    return asdict(job)


@router.delete("/api/syncs/{job_id}")
async def delete_sync(job_id: str, request: Request):
    request.app.state.syncs.delete(job_id)
    await request.app.state.sync.reconcile()
    return {"ok": True}


@router.post("/api/syncs/{job_id}/run")
async def run_sync(job_id: str, request: Request, execute: bool = False):
    # Fire-and-forget onto SyncService's single queue; returns immediately.
    asyncio.create_task(request.app.state.sync.run_job(job_id, execute=execute))
    return JSONResponse({"queued": True}, status_code=202)
