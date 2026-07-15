"""Spotify — playlist source, and (in N-way mode) a writable peer.

Read and write share one OAuth grant (config.SPOTIFY_SCOPE, the set the connector
also requests): spotipy stamps the requesting client's scope onto the cached token
on every refresh, so a read-only client that asked for less would strip the modify
scopes from the cache and make the next writable pass fail. Track dicts produced
here are the common currency the mirror targets consume.
"""

import html
import os
import random
import time

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from . import spotify_web
from .config import DEFAULT_SPOTIFY_REDIRECT_URI, REQUEST_TIMEOUT, SPOTIFY_SCOPE, required_env
from .logs import log, log_note, log_warn

# Connection-level failures spotipy's status-code retry doesn't cover.
_TRANSIENT = (
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ReadTimeout,
)


def _retry(fn, what, attempts=5):
    """Retry a Spotify call on connection resets / read timeouts with backoff —
    reads are idempotent, so a reset page just re-fetches."""
    for attempt in range(attempts):
        try:
            return fn()
        except _TRANSIENT:
            if attempt == attempts - 1:
                raise
            wait = min(2 ** attempt, 20) + random.uniform(0, 2)
            log(f"connection issue ({what}); retrying in {int(wait)}s", tag="spotify")
            time.sleep(wait)


def client(writable=False):
    # Read and write request the same grant (SPOTIFY_SCOPE) so a read-only pass
    # never downscopes the cached token on refresh — see the module docstring.
    auth = SpotifyOAuth(
        client_id=required_env("SPOTIFY_CLIENT_ID"),
        client_secret=required_env("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_SPOTIFY_REDIRECT_URI),
        scope=SPOTIFY_SCOPE,
        cache_path=os.getenv("SPOTIFY_TOKEN_CACHE", ".cache"),
        open_browser=os.getenv("SPOTIFY_OAUTH_OPEN_BROWSER", "1") != "0",
    )
    # With no usable cached token, spotipy prints a URL and calls input() to paste
    # the redirect back — which EOFErrors in a headless server. Pre-check the cache
    # non-interactively so a missing/unrefreshable authorization fails with a clear,
    # actionable message instead of a cryptic EOF.
    try:
        token = auth.validate_token(auth.get_cached_token())
    except Exception:
        token = None
    if not token:
        from .targets.base import TargetAuthError

        detail = ("expired or lacks the write access N-way sync needs" if writable
                  else "missing or expired")
        raise TargetAuthError(
            f"Spotify needs reconnecting — its saved authorization is {detail}. "
            "Reconnect Spotify in the app.")
    return spotipy.Spotify(auth_manager=auth, requests_timeout=REQUEST_TIMEOUT, retries=5)


def description(sp_playlist):
    return html.unescape(sp_playlist.get("description") or "").strip()


def track_total(playlist):
    """Track count from a playlist list-object (the /me/playlists shape), or
    None. The count sits under `items` in the current API response and under
    `tracks` in the older shape — read the new key first, then the legacy one."""
    meta = playlist.get("items") or playlist.get("tracks") or {}
    return meta.get("total")


def playlists_by_name(sp):
    """name (casefolded) -> playlist, preferring playlists I own, then bigger."""
    me = _retry(lambda: sp.current_user(), "current_user")["id"]
    best = {}
    results = _retry(lambda: sp.current_user_playlists(limit=50), "playlists")
    while results:
        for playlist in results.get("items", []):
            if not playlist:
                continue
            name = (playlist.get("name") or "").strip().casefold()
            if not name:
                continue
            rank = (
                (playlist.get("owner") or {}).get("id") == me,
                track_total(playlist) or 0,
            )
            if name not in best or rank > best[name][0]:
                best[name] = (rank, playlist)
        page = results
        results = _retry(lambda: sp.next(page), "playlists page") if results.get("next") else None
    return {name: playlist for name, (rank, playlist) in best.items()}


def all_playlists(sp):
    """Every library playlist (owned AND followed), un-deduped, each annotated with
    `_owned` (its owner is the current user). playlists_by_name collapses by name
    for the sync engine's name-matching; browsing and transfers need the full,
    id-addressable list so a followed playlist that shares a name with an owned one
    stays reachable and can be labelled as followed."""
    me = _retry(lambda: sp.current_user(), "current_user")["id"]
    out = []
    results = _retry(lambda: sp.current_user_playlists(limit=50), "playlists")
    while results:
        for playlist in results.get("items", []):
            if not playlist:
                continue
            playlist["_owned"] = (playlist.get("owner") or {}).get("id") == me
            out.append(playlist)
        page = results
        results = _retry(lambda: sp.next(page), "playlists page") if results.get("next") else None
    return out


def playlist_item_track(item):
    """The track object of a playlist item, handling both the legacy shape
    ({"track": {...}}) and the current Web API shape ({"item": {...}}). Returns
    None for local files, episodes, and ghost entries."""
    track = item.get("track")
    if not isinstance(track, dict):
        track = item.get("item")
    if not isinstance(track, dict):
        return None
    if track.get("type", "track") != "track":
        return None
    if item.get("is_local") or track.get("is_local"):
        return None
    return track


def _playlist_tracks_api(sp, playlist_id):
    tracks = []
    results = _retry(
        lambda: sp.playlist_items(playlist_id, market="from_token", additional_types=("track",), limit=100),
        "playlist_items",
    )
    while results:
        for item in results.get("items", []):
            track = playlist_item_track(item)
            if not track:
                continue
            artists = [a.get("name", "") for a in track.get("artists", []) if a.get("name")]
            tracks.append({
                "id": track.get("id"),
                "isrc": (track.get("external_ids") or {}).get("isrc"),
                "name": track.get("name", ""),
                "artists": artists or [""],
                "album": (track.get("album") or {}).get("name"),
                "duration_ms": track.get("duration_ms"),
                "added_at": item.get("added_at") or "",
            })
        page = results
        results = _retry(lambda: sp.next(page), "tracks page") if results.get("next") else None
    return tracks


def playlist_tracks(sp, playlist_id):
    """Playlist tracks via the official API, falling back to the web-player read
    on a 403 — which is what the official API returns for the tracks of a followed
    (non-owned) playlist under a Development-Mode app. The fallback (SpotifyScraper)
    is opt-outable via SPOTIFY_WEB_FALLBACK; on any fallback failure the original
    403 is re-raised so the caller's safety guards still apply."""
    try:
        return _playlist_tracks_api(sp, playlist_id)
    except spotipy.SpotifyException as e:
        if e.http_status == 403 and spotify_web.enabled():
            log_note(f"{playlist_id}: official read forbidden (403); trying web-player fallback", tag="spotify")
            try:
                tracks = spotify_web.playlist_tracks(playlist_id)
                log_note(f"{playlist_id}: web-player fallback read {len(tracks)} tracks", tag="spotify")
                return tracks
            except Exception as we:
                log_warn(f"{playlist_id}: web-player fallback failed ({we!r})", tag="spotify")
                raise e
        raise
