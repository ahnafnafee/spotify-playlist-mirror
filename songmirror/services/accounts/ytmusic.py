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
              help="Google Cloud OAuth client, type 'TVs and Limited Input devices'"),
        Field("YTMUSIC_OAUTH_CLIENT_SECRET", "OAuth client secret", secret=True,
              help="Same OAuth client's secret"),
    ]

    def _auth_file(self):
        # os.getenv first so Docker's YTMUSIC_AUTH_FILE=/data/... (the persistent
        # volume) wins over a relative default that would land in an ephemeral dir.
        return os.getenv("YTMUSIC_AUTH_FILE") or self._store.get("YTMUSIC_AUTH_FILE") or "data/ytmusic_oauth.json"

    def _creds(self):
        from ytmusicapi.auth.oauth import OAuthCredentials

        return OAuthCredentials(
            client_id=self._store.get("YTMUSIC_OAUTH_CLIENT_ID"),
            client_secret=self._store.get("YTMUSIC_OAUTH_CLIENT_SECRET"),
        )

    def _browser_path(self):
        return (os.getenv("YTMUSIC_BROWSER_AUTH") or self._store.get("YTMUSIC_BROWSER_AUTH")
                or "data/ytmusic_browser.json")

    def _browser_active(self):
        pref = str(self._store.get("YTMUSIC_PREFER_BROWSER") or os.getenv("YTMUSIC_PREFER_BROWSER") or "")
        return pref.lower() in ("1", "on", "true", "yes") and os.path.exists(self._browser_path())

    def status(self) -> ConnStatus:
        if self._browser_active():
            return ConnStatus("connected", "no-quota (browser cookies) mode")
        if not self._configured("YTMUSIC_OAUTH_CLIENT_ID", "YTMUSIC_OAUTH_CLIENT_SECRET"):
            return ConnStatus("unconfigured")
        if os.path.exists(self._auth_file()):
            return ConnStatus("connected", "token present")
        return ConnStatus("unconfigured", "not authorized yet")

    def enable_browser(self, headers_raw: str) -> ConnStatus:
        """Turn on the no-quota (youtubei) backend from pasted music.youtube.com
        request headers: parse them into a ytmusicapi browser-auth file, validate
        the cookies with one authenticated call, then flip YTMUSIC_PREFER_BROWSER."""
        import ytmusicapi
        from ytmusicapi import YTMusic

        if not (headers_raw or "").strip():
            return ConnStatus("error", "paste the request headers from music.youtube.com first")
        path = self._browser_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            ytmusicapi.setup(filepath=path, headers_raw=headers_raw)
        except Exception as e:
            return ConnStatus("error", f"couldn't parse those headers ({e!r})")
        try:
            YTMusic(path).get_library_playlists(limit=1)  # cookies valid?
        except Exception as e:
            return ConnStatus("error", f"YouTube Music rejected the cookies ({e!r})")
        self._store.save({"YTMUSIC_BROWSER_AUTH": path, "YTMUSIC_PREFER_BROWSER": "1"})
        return ConnStatus("connected", "no-quota (browser cookies) mode")

    def disable_browser(self) -> ConnStatus:
        """Revert to the durable OAuth Data API; the cookie file is left in place
        so re-enabling doesn't need another paste."""
        self._store.save({"YTMUSIC_PREFER_BROWSER": "0"})
        return self.status()

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
        # Before the user authorizes, token_from_code returns an error dict
        # (e.g. {"error": "authorization_pending"}) instead of raising. Building a
        # RefreshingToken from that 500s the poll — which stops the UI's poll loop
        # even though auth later succeeds. Treat a non-token response as "keep waiting".
        if not isinstance(raw, dict) or "access_token" not in raw:
            detail = raw.get("error", "pending") if isinstance(raw, dict) else "pending"
            return ConnStatus("unconfigured", f"waiting for authorization ({detail})")
        params = set(inspect.signature(RefreshingToken.__init__).parameters) - {"self", "credentials", "_local_cache"}
        token = RefreshingToken(credentials=creds, **{k: v for k, v in raw.items() if k in params})
        path = self._auth_file()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        token.store_token(path)
        return ConnStatus("connected", "authorized")
