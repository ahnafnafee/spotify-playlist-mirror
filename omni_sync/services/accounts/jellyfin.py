"""Jellyfin connector (api_key) — optional, for pushing playlist covers."""

import requests

from .base import ConnStatus, Connector, Field


class JellyfinConnector(Connector):
    id = "jellyfin"
    name = "Jellyfin"
    auth_kind = "api_key"
    config_fields = [
        Field("JELLYFIN_URL", "Server URL", help="e.g. http://localhost:8096 (in Docker: http://host.docker.internal:8096)"),
        Field("JELLYFIN_API_KEY", "API key", secret=True, help="Jellyfin Dashboard → API Keys → New"),
        Field("JELLYFIN_USER_ID", "User ID", required=False, help="Optional; only if listing playlists needs it"),
    ]

    def status(self) -> ConnStatus:
        if not self._configured("JELLYFIN_URL", "JELLYFIN_API_KEY"):
            return ConnStatus("unconfigured")
        return ConnStatus("connected", self._store.get("JELLYFIN_URL"))

    def submit(self, values: dict) -> ConnStatus:
        self._store.save({k: values.get(k) for k in ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_USER_ID")})
        ok, detail = self._ping()
        return ConnStatus("connected" if ok else "error", detail)

    def _ping(self):
        url = (self._store.get("JELLYFIN_URL") or "").rstrip("/")
        key = self._store.get("JELLYFIN_API_KEY")
        if not (url and key):
            return False, "missing url/key"
        try:
            r = requests.get(f"{url}/System/Info", headers={"X-Emby-Token": key}, timeout=10)
            return r.ok, "" if r.ok else f"HTTP {r.status_code}"
        except Exception as e:
            return False, repr(e)
