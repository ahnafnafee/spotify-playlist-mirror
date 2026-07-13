"""Spotify as a writable mirror peer (N-way sync only).

In one-way mode Spotify is just the source and this target isn't built. In
N-way mode it becomes a first-class peer: the same reconcile that edits Apple
and YouTube Music also adds/removes on Spotify. Reads reuse the helpers in
spotify.py; writes go through spotipy's playlist-modify endpoints and therefore
need the modify scopes (see spotify.client(writable=True)).
"""

import spotipy

from .. import spotify
from ..config import polite_sleep
from ..matching import normalize_text, romanized, score_candidate, track_key
from .base import MirrorTarget, TargetAuthError


def _uri(track_id):
    return track_id if str(track_id).startswith("spotify:") else f"spotify:track:{track_id}"


class SpotifyTarget(MirrorTarget):
    name = "Spotify"
    tag = "spotify"
    source = "spotify"

    def __init__(self, sp, cache_file):
        self._sp = sp
        self.cache_file = cache_file
        self._me = None

    def _user(self):
        if self._me is None:
            self._me = spotify._retry(lambda: self._sp.current_user(), "current_user")["id"]
        return self._me

    def _write(self, fn, what):
        """Run a mutation; map an auth/scope rejection to the fail-closed path."""
        try:
            return spotify._retry(fn, what)
        except spotipy.SpotifyException as e:
            if e.http_status in (401, 403):
                raise TargetAuthError(
                    f"Spotify rejected {what} ({e.http_status}). N-way mode needs the playlist-modify "
                    "scopes — delete the token cache (data/spotify_token_cache) and re-run the OAuth flow."
                ) from e
            raise

    # -- MirrorTarget ----------------------------------------------------------
    def list_playlists(self):
        return spotify.playlists_by_name(self._sp)

    def is_editable(self, playlist):
        owner = (playlist.get("owner") or {}).get("id")
        return owner is None or owner == self._user()

    def playlist_count(self, playlist):
        return (playlist.get("tracks") or {}).get("total")

    def create(self, sp_playlist):
        pl = self._write(
            lambda: self._sp.user_playlist_create(self._user(), sp_playlist.get("name", ""),
                                                  public=False, description=spotify.description(sp_playlist)),
            "create playlist")
        polite_sleep(1.0)
        return pl

    def playlist_tracks(self, playlist):
        return spotify.playlist_tracks(self._sp, playlist["id"])

    def track_id(self, track):
        return track.get("id")

    def resolve(self, track, cache):
        primary = track["artists"][0] if track["artists"] else ""
        if not f"{track['name']} {primary}".strip():
            return None, None
        key = track_key(track["name"], " ".join(track["artists"]))
        if key in cache["search"]:
            return cache["search"][key], "search"
        best, method = self._search(track, primary)
        cache["search"][key] = best
        cache["dirty"] = True
        polite_sleep(0.3)
        return best, method

    def _search(self, track, primary):
        isrc = track.get("isrc")
        if isrc:  # the hard cross-walk when the originating provider carried an ISRC
            best = self._best(track, self._query(f"isrc:{isrc}"))
            if best:
                return best, "isrc"
        base = f"{track['name']} {primary}".strip()
        queries = [f'track:{track["name"]} artist:{primary}'.strip(), base]
        rom = f"{romanized(track['name'])} {romanized(primary)}".strip()
        if rom and rom != normalize_text(base):
            queries.append(rom)
        for q in queries:
            best = self._best(track, self._query(q))
            if best:
                return best, "search"
        return None, None

    def _query(self, q):
        try:
            res = spotify._retry(lambda: self._sp.search(q=q, type="track", limit=8), "search")
        except spotipy.SpotifyException:
            return []
        return (res.get("tracks") or {}).get("items", [])

    def _best(self, track, items):
        best_id, best_score = None, -1.0
        for it in items:
            arts = [a.get("name", "") for a in it.get("artists", []) if a.get("name")]
            score, ok = score_candidate(track["name"], track["artists"], track["duration_ms"],
                                        it.get("name", ""), ", ".join(arts), it.get("duration_ms"))
            if ok and score > best_score:
                best_id, best_score = it.get("id"), score
        return best_id

    def add(self, playlist, target_ids):
        for tid in target_ids:  # one at a time preserves date-added order
            self._write(lambda t=tid: self._sp.playlist_add_items(playlist["id"], [_uri(t)]), "add")
            polite_sleep(0.3)

    def remove(self, playlist, track):
        tid = self.track_id(track)
        if not tid:
            return
        self._write(lambda: self._sp.playlist_remove_all_occurrences_of_items(playlist["id"], [_uri(tid)]), "remove")
        polite_sleep(0.3)
