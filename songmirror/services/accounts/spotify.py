"""Spotify connector (oauth_redirect) — the browser handshake over spotipy.

Self-hosting note: the user registers their own Spotify app and pastes its
client id/secret once; the wizard shows the exact redirect URI to whitelist.
"""

import os

from ...engine.config import SPOTIFY_SCOPE
from .base import ConnStatus, Connector, Field


class SpotifyConnector(Connector):
    id = "spotify"
    name = "Spotify"
    auth_kind = "oauth_redirect"
    config_fields = [
        Field("SPOTIFY_CLIENT_ID", "Client ID",
              help="From your app at developer.spotify.com/dashboard → Settings"),
        Field("SPOTIFY_CLIENT_SECRET", "Client secret", secret=True,
              help="Same page — click 'View client secret'"),
    ]

    def _token_cache(self):
        # os.getenv first so Docker's SPOTIFY_TOKEN_CACHE=/data/... (the persistent
        # volume, and where the engine reads the token) wins over a relative default
        # that would resolve to an ephemeral, possibly-missing dir in the container.
        return os.getenv("SPOTIFY_TOKEN_CACHE") or self._store.get("SPOTIFY_TOKEN_CACHE") or "data/spotify_token_cache"

    def _oauth(self, redirect_uri):
        from spotipy.oauth2 import SpotifyOAuth

        cache = self._token_cache()
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)  # spotipy silently skips caching if the parent dir is missing
        # Grant the full read+write set up front (SPOTIFY_SCOPE, shared with the
        # engine client). Reads cover the user's own private and collaborative
        # playlists (followed playlists stay unreadable — a Spotify dev-mode limit,
        # not a scope gap); modify is needed whenever Spotify is a write target.
        # Granting once avoids a re-auth when a later sync makes Spotify writable,
        # and — because engine and connector request the identical scope — spotipy's
        # per-refresh scope rewrite can never narrow the cached token.
        return SpotifyOAuth(
            client_id=self._store.get("SPOTIFY_CLIENT_ID"),
            client_secret=self._store.get("SPOTIFY_CLIENT_SECRET"),
            redirect_uri=redirect_uri,
            scope=SPOTIFY_SCOPE,
            cache_path=cache,
            open_browser=False,
        )

    def status(self) -> ConnStatus:
        if not self._configured("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
            return ConnStatus("unconfigured")
        if os.path.exists(self._token_cache()):
            return ConnStatus("connected", "token present")
        return ConnStatus("unconfigured", "not authorized yet")

    def begin_redirect(self, redirect_uri: str) -> str:
        self._store.save({"SPOTIFY_REDIRECT_URI": redirect_uri})
        return self._oauth(redirect_uri).get_authorize_url()

    def complete_redirect(self, params: dict) -> ConnStatus:
        redirect_uri = self._store.get("SPOTIFY_REDIRECT_URI")
        oauth = self._oauth(redirect_uri)
        code = oauth.parse_response_code(params.get("url") or params.get("code") or "")
        oauth.get_access_token(code, as_dict=False, check_cache=False)  # writes the token cache
        return ConnStatus("connected", "authorized")
