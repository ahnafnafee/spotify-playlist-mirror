"""Server-Sent Events — the live sync feed."""

import asyncio
import json
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


def _fmt(event) -> str:
    return f"data: {json.dumps(asdict(event))}\n\n"


@router.get("/events")
async def events(request: Request):
    bus = request.app.state.bus
    q = bus.subscribe()

    async def gen():
        try:
            yield ": connected\n\n"  # flush headers immediately
            for e in bus.recent():  # backfill the current/last pass
                yield _fmt(e)
            # Poll disconnect each second so a closed browser is noticed promptly
            # (blocking on q.get for long would strand the generator).
            while not await request.is_disconnected():
                try:
                    yield _fmt(await asyncio.wait_for(q.get(), timeout=1.0))
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
