"""Playlist browsing + explicit cross-service pairing.

Browse reuses each provider's existing list_playlists; pairing lets the user link
differently-named playlists and set a per-pair direction, overriding the default
same-name matching. Services tier — drives the engine (build_one), never the web.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..engine import spotify
from ..engine.config import parse_args
from ..engine.targets import build_one
from .settings import _open_private


# ponytail: provider playlist dicts store name/id differently (Spotify `name`,
# Apple `attributes.name`, YT `title`/`playlistId`). Read defensively here until
# Phase 3 adds playlist_name/playlist_id accessors to the MirrorTarget protocol.
def _pl_name(pl):
    return pl.get("name") or (pl.get("attributes") or {}).get("name") or pl.get("title") or ""


def _pl_id(pl):
    return pl.get("id") or pl.get("playlistId") or _pl_name(pl)


class PlaylistService:
    def __init__(self, settings):
        self._settings = settings

    def browse(self, provider_id):
        """[{id, name, count}] for one connected provider (empty if unconfigured)."""
        self._settings.apply_to_env()
        opts = parse_args([])
        sp = None
        if provider_id == "spotify":
            try:
                sp = spotify.client()
            except Exception:
                return []
        target = build_one(provider_id, opts, sp)
        if target is None:
            return []
        try:
            by_name = target.list_playlists()
        except Exception:
            return []
        rows = [
            {"id": _pl_id(pl), "name": _pl_name(pl), "count": target.playlist_count(pl)}
            for pl in by_name.values()
        ]
        return sorted(rows, key=lambda r: (r["name"] or "").casefold())


@dataclass
class PlaylistLink:
    name: str
    members: dict = field(default_factory=dict)  # provider_id -> playlist_id | None (None = create by name)
    direction: str = "oneway"                     # oneway | nway
    source: str | None = "spotify"
    enabled: bool = True
    id: str = ""


class LinkStore:
    """Explicit pairings persisted to data/links.json (owner-only, alongside the
    other data-dir state)."""

    def __init__(self, dir="data"):
        self._path = Path(dir) / "links.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def list(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                return [PlaylistLink(**d) for d in json.load(f)]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def upsert(self, link):
        if not link.id:
            link.id = uuid.uuid4().hex[:8]
        links = [l for l in self.list() if l.id != link.id]
        links.append(link)
        self._save(links)
        return link

    def delete(self, link_id):
        self._save([l for l in self.list() if l.id != link_id])

    def _save(self, links):
        with _open_private(self._path) as f:
            json.dump([asdict(l) for l in links], f, indent=2)
