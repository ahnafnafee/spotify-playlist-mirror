"""Apple Music target — via the web player's amp-api (captured web tokens, no
Apple Developer account). Writes go to the same endpoints music.apple.com uses.
"""

import random
import time

import requests

from ..config import AMP, REQUEST_TIMEOUT, polite_sleep, required_env
from ..logs import log, log_warn
from ..matching import normalize_text, romanized, score_candidate
from .base import MirrorTarget, TargetAuthError


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _headers():
    bearer = required_env("APPLE_BEARER_TOKEN")
    if bearer.lower().startswith("bearer "):
        bearer = bearer[7:]
    return {
        "Authorization": f"Bearer {bearer}",
        "Media-User-Token": required_env("APPLE_USER_TOKEN"),
        "Origin": "https://music.apple.com",
        "Referer": "https://music.apple.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    }


class AppleMusicTarget(MirrorTarget):
    name = "Apple Music"
    tag = "apple"
    source = "apple"

    def __init__(self, storefront, cache_file):
        self.storefront = storefront
        self.cache_file = cache_file
        # One pooled session (keep-alive) for the whole pass — opening a fresh
        # TCP/TLS connection per request is what triggers Apple's connection
        # resets under the ~100+ calls a big playlist needs. Headers read env
        # now so re-captured tokens are picked up per pass.
        self._session = requests.Session()
        self._session.headers.update(_headers())
        self._search_throttled = False  # set once catalog search rate-limits; defer the rest of the pass

    # -- HTTP ------------------------------------------------------------------
    def _request(self, method, url, *, params=None, json_body=None, ok404=False):
        """One amp-api call over the pooled session. GETs retry with exponential
        backoff on network resets / 5xx; 429s back off on every method (a
        rate-limited call never executed); other mutation failures are
        single-shot — a lost add/remove self-heals next pass, a blindly retried
        one could double-apply."""
        attempts = 5
        for attempt in range(attempts):
            try:
                r = self._session.request(method, url, params=params, json=json_body, timeout=REQUEST_TIMEOUT)
            except requests.RequestException:
                # Connection reset / blip: retry GETs (idempotent) with backoff.
                if method == "GET" and attempt < attempts - 1:
                    time.sleep(min(2 ** attempt, 20) + random.uniform(0, 2))
                    continue
                raise
            if r.status_code in (401, 403):
                raise TargetAuthError(
                    f"Apple rejected {method} {url.split('/v1/')[-1]} ({r.status_code}). "
                    "Re-capture APPLE_BEARER_TOKEN / APPLE_USER_TOKEN from music.apple.com DevTools."
                )
            if r.status_code == 404 and ok404:
                return None
            if r.status_code == 429 and attempt < 1:
                # One short retry for a transient blip; a sustained catalog-search
                # limit is handled by the resolver deferring the rest of the pass.
                wait = float(r.headers.get("Retry-After") or 10) + random.uniform(1, 4)
                log(f"  rate-limited by Apple; waiting {int(wait)}s", tag=self.tag)
                time.sleep(wait)
                continue
            if r.status_code >= 500 and method == "GET" and attempt < attempts - 1:
                time.sleep(min(2 ** attempt, 20) + random.uniform(0, 2))
                continue
            r.raise_for_status()
            return r
        return None

    # -- MirrorTarget ----------------------------------------------------------
    def list_playlists(self):
        out, offset = {}, 0
        while True:
            r = self._request("GET", f"{AMP}/me/library/playlists", params={"limit": 100, "offset": offset})
            data = r.json()
            for pl in data.get("data", []):
                key = (pl.get("attributes", {}).get("name") or "").strip().casefold()
                if key and key not in out:
                    out[key] = pl
            if "next" not in data:
                return out
            offset += 100

    def is_editable(self, playlist):
        return playlist.get("attributes", {}).get("canEdit") is not False

    def create(self, sp_playlist):
        from .. import spotify

        attributes = {"name": sp_playlist.get("name", "")}
        desc = spotify.description(sp_playlist)
        if desc:
            attributes["description"] = desc
        r = self._request("POST", f"{AMP}/me/library/playlists", json_body={"attributes": attributes})
        return r.json()["data"][0]

    def playlist_tracks(self, playlist):
        tracks, offset = [], 0
        while True:
            r = self._request("GET", f"{AMP}/me/library/playlists/{playlist['id']}/tracks",
                              params={"limit": 100, "offset": offset}, ok404=True)
            if r is None:  # empty playlists 404 this endpoint
                return tracks
            data = r.json()
            for t in data.get("data", []):
                attrs = t.get("attributes", {})
                pp = attrs.get("playParams", {})
                tracks.append({
                    "relationship_id": t.get("id"),
                    "catalog_id": pp.get("catalogId") or pp.get("id"),
                    "name": attrs.get("name", ""),
                    "artist": attrs.get("artistName", ""),
                    "album": attrs.get("albumName"),
                    "duration_ms": attrs.get("durationInMillis"),
                })
            if "next" not in data:
                return tracks
            offset += 100

    def track_id(self, track):
        return track.get("catalog_id")

    def playlist_count(self, playlist):
        return playlist.get("attributes", {}).get("trackCount")

    def playlist_name(self, playlist):
        return playlist.get("attributes", {}).get("name", "")

    def playlist_description(self, playlist):
        return (playlist.get("attributes", {}).get("description") or {}).get("standard", "")

    def prefetch(self, sp_tracks, cache):
        """Batch-resolve ISRCs to catalog candidates via filter[isrc]. Results
        (including empties) are cached forever — ISRCs don't change."""
        isrcs = sorted({t["isrc"] for t in sp_tracks if t["isrc"]})
        missing = [i for i in isrcs if i not in cache["isrc"]]
        for chunk in _chunks(missing, 25):
            r = self._request("GET", f"{AMP}/catalog/{self.storefront}/songs",
                             params={"filter[isrc]": ",".join(chunk)})
            found = {}
            for song in r.json().get("data", []):
                attrs = song.get("attributes", {})
                isrc = attrs.get("isrc")
                if isrc:
                    found.setdefault(isrc, []).append({
                        "id": song.get("id"), "name": attrs.get("name", ""),
                        "artist": attrs.get("artistName", ""), "duration_ms": attrs.get("durationInMillis"),
                    })
            for isrc in chunk:
                cache["isrc"][isrc] = found.get(isrc, [])
            cache["dirty"] = True
            polite_sleep(0.25)

    def native_isrc_map(self, cache):
        # Apple library reads omit ISRC, but its filter[isrc] resolve cache maps
        # ISRC -> catalog candidates; reverse it to catalog_id -> ISRC.
        out = {}
        for isrc, cands in (cache.get("isrc") or {}).items():
            for c in cands:
                if c.get("id"):
                    out.setdefault(c["id"], isrc)
        return out

    def expected_ids(self, sp_tracks, links, cache):
        out = {}
        for t in sp_tracks:
            ids = set()
            if links.get(t.get("id")):
                ids.add(links[t["id"]])
            for c in cache["isrc"].get(t.get("isrc") or "", []):
                if c.get("id"):
                    ids.add(c["id"])
            if ids:
                out[t.get("id")] = ids
        return out

    def resolve(self, track, cache):
        candidates = [c for c in cache["isrc"].get(track["isrc"] or "", []) if c.get("id")]
        if candidates and track["duration_ms"] is not None:
            candidates.sort(key=lambda c: abs((c.get("duration_ms") or 0) - track["duration_ms"]))
        if candidates:
            return candidates[0]["id"], "isrc"
        return self._search(track["name"], track["artists"], track["duration_ms"], cache), "search"

    def _search_once(self, term, name, artists, duration_ms):
        r = self._request("GET", f"{AMP}/catalog/{self.storefront}/search",
                         params={"term": term, "types": "songs", "limit": 10, "l": "en-us"})
        best_id, best_score = None, -1.0
        for song in r.json().get("results", {}).get("songs", {}).get("data", []):
            attrs = song.get("attributes", {})
            score, ok = score_candidate(name, artists, duration_ms,
                                        attrs.get("name", ""), attrs.get("artistName", ""),
                                        attrs.get("durationInMillis"))
            if ok and score > best_score:
                best_id, best_score = song.get("id"), score
        return best_id

    def _search(self, name, artists, duration_ms, cache):
        primary = artists[0] if artists else ""
        if not f"{name} {primary}".strip():
            return None  # amp-api 400s on an empty term
        key = f"{name}|{primary}".casefold()
        if key in cache["search"]:
            return cache["search"][key]
        if self._search_throttled:
            return None  # catalog search rate-limited earlier this pass; defer to the next (don't cache a miss)
        try:
            best = self._search_once(f"{name} {primary}".strip(), name, artists, duration_ms)
            if not best:
                rom = f"{romanized(name)} {romanized(primary)}".strip()
                if rom and rom != normalize_text(f"{name} {primary}"):
                    polite_sleep(0.3)
                    best = self._search_once(rom, name, artists, duration_ms)
        except requests.HTTPError as e:
            if "429" not in str(e):
                raise
            self._search_throttled = True
            log_warn("Apple Music search rate-limited — deferring the rest of the resolves to the next pass",
                     tag=self.tag)
            return None
        cache["search"][key] = best
        cache["dirty"] = True
        polite_sleep(0.3)
        return best

    def add(self, playlist, target_ids):
        # One POST per track — batched arrays can land out of order, and append
        # order is what keeps the playlist sorted by date added.
        for catalog_id in target_ids:
            self._request("POST", f"{AMP}/me/library/playlists/{playlist['id']}/tracks",
                         json_body={"data": [{"id": catalog_id, "type": "songs"}]})
            polite_sleep(0.4)

    def remove(self, playlist, track):
        self._request("DELETE", f"{AMP}/me/library/playlists/{playlist['id']}/tracks",
                     params={"ids[library-songs]": track["relationship_id"], "mode": "all"})
        polite_sleep(0.4)
