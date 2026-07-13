"""YouTube Music connector (oauth_device) — Google's limited-input device flow.

Cleanest of the three: show a code + URL, poll until the user authorizes on
another device, then persist the refresh token where the engine reads it.
"""

import inspect
import os

from .base import ConnStatus, Connector, DeviceCode, Field


class YTMusicConnector(Connector):
    id = "ytmusic"
    name = "YouTube Music"
    auth_kind = "oauth_device"
    config_fields = [
        Field("YTMUSIC_OAUTH_CLIENT_ID", "OAuth client ID",
              help="Google Cloud → 'TVs and Limited Input devices' OAuth client"),
        Field("YTMUSIC_OAUTH_CLIENT_SECRET", "OAuth client secret", secret=True),
    ]

    def _auth_file(self):
        return self._store.get("YTMUSIC_AUTH_FILE") or "data/ytmusic_oauth.json"

    def _creds(self):
        from ytmusicapi.auth.oauth import OAuthCredentials

        return OAuthCredentials(
            client_id=self._store.get("YTMUSIC_OAUTH_CLIENT_ID"),
            client_secret=self._store.get("YTMUSIC_OAUTH_CLIENT_SECRET"),
        )

    def status(self) -> ConnStatus:
        if not self._configured("YTMUSIC_OAUTH_CLIENT_ID", "YTMUSIC_OAUTH_CLIENT_SECRET"):
            return ConnStatus("unconfigured")
        if os.path.exists(self._auth_file()):
            return ConnStatus("connected", "token present")
        return ConnStatus("unconfigured", "not authorized yet")

    def begin_device(self) -> DeviceCode:
        code = self._creds().get_code()
        return DeviceCode(
            user_code=code["user_code"],
            verification_url=code["verification_url"],
            device_code=code["device_code"],
            interval=code.get("interval", 5),
        )

    def poll_device(self, dc: DeviceCode) -> ConnStatus:
        from ytmusicapi.auth.oauth import RefreshingToken

        creds = self._creds()
        try:
            raw = creds.token_from_code(dc.device_code)
        except Exception as e:
            return ConnStatus("unconfigured", f"waiting for authorization ({e!r})")
        params = set(inspect.signature(RefreshingToken.__init__).parameters) - {"self", "credentials", "_local_cache"}
        token = RefreshingToken(credentials=creds, **{k: v for k, v in raw.items() if k in params})
        path = self._auth_file()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        token.store_token(path)
        return ConnStatus("connected", "authorized")
