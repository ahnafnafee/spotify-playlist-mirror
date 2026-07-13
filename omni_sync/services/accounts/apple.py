"""Apple Music connector (token_paste) — Apple can't be OAuthed here, so the
wizard guides pasting the web player's bearer + Media-User-Token."""

import requests

from ...engine.config import AMP
from .base import ConnStatus, Connector, Field


class AppleConnector(Connector):
    id = "apple"
    name = "Apple Music"
    auth_kind = "token_paste"
    config_fields = [
        Field("APPLE_BEARER_TOKEN", "Bearer token", secret=True,
              help="Value of the 'authorization' request header"),
        Field("APPLE_USER_TOKEN", "Media-User-Token", secret=True,
              help="Value of the 'media-user-token' request header"),
        Field("APPLE_STOREFRONT", "Storefront", required=False,
              help="Your country code, e.g. us"),
    ]

    def status(self) -> ConnStatus:
        if not self._configured("APPLE_BEARER_TOKEN", "APPLE_USER_TOKEN"):
            return ConnStatus("unconfigured")
        ok, detail = self._validate()
        return ConnStatus("connected", detail) if ok else ConnStatus("expired", detail)

    def submit(self, values: dict) -> ConnStatus:
        self._store.save({k: values.get(k) for k in ("APPLE_BEARER_TOKEN", "APPLE_USER_TOKEN", "APPLE_STOREFRONT")})
        ok, detail = self._validate()
        return ConnStatus("connected", detail) if ok else ConnStatus("error", detail or "token rejected")

    def _validate(self):
        bearer = self._store.get("APPLE_BEARER_TOKEN") or ""
        user = self._store.get("APPLE_USER_TOKEN") or ""
        if not (bearer and user):
            return False, "missing tokens"
        if bearer.lower().startswith("bearer "):
            bearer = bearer[7:]
        try:
            r = requests.get(
                f"{AMP}/me/library/playlists?limit=1",
                headers={"Authorization": f"Bearer {bearer}", "Media-User-Token": user,
                         "Origin": "https://music.apple.com"},
                timeout=15,
            )
            return r.ok, "" if r.ok else f"HTTP {r.status_code}"
        except Exception as e:
            return False, repr(e)
