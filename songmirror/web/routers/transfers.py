"""One-off playlist transfers + conflict resolution."""

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/transfers")
async def start_transfer(request: Request, body: dict = Body(...)):
    # async so submit()'s asyncio.create_task has a running loop (a sync endpoint
    # runs in a threadpool with no loop and would 500).
    job = request.app.state.transfers.submit({
        "source_provider": body["source_provider"],
        "source_playlist_id": body["source_playlist_id"],
        "dest_provider": body["dest_provider"],
        "dest_playlist_id": body.get("dest_playlist_id"),
        "dest_name": body.get("dest_name", ""),
    })
    return JSONResponse({"job_id": job["id"]}, status_code=202)


@router.get("/api/transfers")
def list_transfers(request: Request):
    """Active transfers (queued/running/paused) — the dashboard's ongoing list."""
    return request.app.state.transfers.list_active()


@router.get("/api/transfers/{job_id}")
def transfer_status(job_id: str, request: Request):
    job = request.app.state.transfers.get(job_id)
    if not job:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return request.app.state.transfers.public(job)


@router.post("/api/transfers/{job_id}/resolve")
def resolve_conflict(job_id: str, request: Request, body: dict = Body(...)):
    # Declared before the generic /{action} route below so it isn't shadowed
    # (FastAPI matches routes in declaration order).
    return {"ok": request.app.state.transfers.resolve(job_id, body["key"], body["dest_id"])}


@router.post("/api/transfers/{job_id}/{action}")
def control_transfer(job_id: str, action: str, request: Request):
    """Pause / resume / stop a transfer. Returns {ok} — ok is False when the
    action doesn't apply to the job's current state (e.g. resume on a done job)."""
    svc = request.app.state.transfers
    fn = {"pause": svc.pause, "resume": svc.resume, "stop": svc.stop}.get(action)
    if fn is None:
        return JSONResponse({"detail": "unknown action"}, status_code=404)
    return {"ok": fn(job_id)}
