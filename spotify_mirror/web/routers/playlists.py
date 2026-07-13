"""Playlist browsing + pairing-link CRUD."""

from dataclasses import asdict

from fastapi import APIRouter, Body, Request

from ...services.playlists import PlaylistLink, PlaylistService

router = APIRouter()


@router.get("/api/playlists")
def playlists(request: Request, provider: str):
    return PlaylistService(request.app.state.settings).browse(provider)


@router.get("/api/links")
def list_links(request: Request):
    return [asdict(link) for link in request.app.state.links.list()]


@router.put("/api/links")
def upsert_link(request: Request, body: dict = Body(...)):
    link = PlaylistLink(
        name=body["name"],
        members=body.get("members", {}),
        direction=body.get("direction", "oneway"),
        source=body.get("source", "spotify"),
        enabled=body.get("enabled", True),
        id=body.get("id", ""),
    )
    return asdict(request.app.state.links.upsert(link))


@router.delete("/api/links/{link_id}")
def delete_link(request: Request, link_id: str):
    request.app.state.links.delete(link_id)
    return {"ok": True}
