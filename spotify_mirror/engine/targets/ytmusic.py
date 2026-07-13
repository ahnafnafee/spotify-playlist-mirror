"""YouTube Music target — hybrid: Data API v3 for reads/writes, ytmusicapi for search.

The playlist reads and writes (list/create/add/remove) go through the official
YouTube Data API v3 with a durable OAuth refresh token — ytmusicapi's internal
youtubei API rejects self-made OAuth clients (HTTP 400) and its browser cookies
die within a day, so neither survives an unattended write loop. Its writes
share YouTube's playlist/video namespace, so they show up in the YouTube Music
app.

Resolution (matching a track to a video id) instead uses ytmusicapi's PUBLIC,
unauthenticated search. Two reasons: it costs no Data API quota (the killer
constraint — a Data API search is 100 of only 10k units/day), and it returns
real catalog songs (`- Topic` art-tracks) with durations, so matches are both
free and higher quality than the Data API's video search.

Setup: create a Google "TVs and Limited Input devices" OAuth client, then
    uvx ytmusicapi oauth --file data/ytmusic_oauth.json \
        --client-id <ID> --client-secret <SECRET>
and set YTMUSIC_OAUTH_CLIENT_ID / YTMUSIC_OAUTH_CLIENT_SECRET.
"""

import json
import os
import random
import re
import time

import requests

from ..config import REQUEST_TIMEOUT, polite_sleep
from ..logs import log, log_note, log_warn
from ..matching import normalize_text, romanized, score_candidate, track_key
from .base import MirrorTarget, TargetAuthError

DEFAULT_AUTH_FILE = "ytmusic_oauth.json"
API = "https://www.googleapis.com/youtube/v3"

_TOPIC_RE = re.compile(r"\s*-\s*Topic$")


def build():
    """A ready YTMusicTarget, or None (logged) when YT isn't set up."""
    auth = os.getenv("YTMUSIC_AUTH_FILE", DEFAULT_AUTH_FILE)
    cid, secret = os.getenv("YTMUSIC_OAUTH_CLIENT_ID"), os.getenv("YTMUSIC_OAUTH_CLIENT_SECRET")
    if not os.path.exists(auth):
        log_note(f"YouTube Music skipped: no OAuth token '{auth}' (create with: "
                 "uvx ytmusicapi oauth --file data/ytmusic_oauth.json --client-id ... --client-secret ...)", tag="yt")
        return None
    if not (cid and secret):
        log_note("YouTube Music skipped: set YTMUSIC_OAUTH_CLIENT_ID and YTMUSIC_OAUTH_CLIENT_SECRET", tag="yt")
        return None
    try:
        from ytmusicapi.auth.oauth import OAuthCredentials
    except ImportError:
        log_note("YouTube Music skipped: ytmusicapi not installed", tag="yt")
        return None
    try:
        return YTMusicTarget(auth, OAuthCredentials(client_id=cid, client_secret=secret))
    except Exception as e:
        log_warn(f"YouTube Music unavailable (re-run the ytmusicapi oauth setup?): {e!r}", tag="yt")
        return None


def _parse_count(value):
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _artist_from_channel(channel):
    """'The Cranberries - Topic' -> 'The Cranberries'; VEVO/plain kept as-is."""
    return _TOPIC_RE.sub("", channel or "").strip()


def _err_reason(response):
    try:
        errors = response.json().get("error", {}).get("errors", [])
        return errors[0].get("reason", "") if errors else ""
    except ValueError:
        return ""


def _with_backoff(fn, what):
    """Retry a ytmusicapi search past YouTube's bot-detection throttle (403/429).
    This path spends no Data API quota — the limit here is IP-based, not the
    daily unit budget — so backing off and retrying is worthwhile."""
    for attempt in range(4):
        try:
            return fn()
        except Exception as e:
            if not any(code in str(e) for code in ("403", "429")) or attempt == 3:
                raise
            wait = 15 * (2 ** attempt) + random.uniform(0, 8)
            log(f"  YT search throttled ({what}); backing off {int(wait)}s", tag="yt")
            time.sleep(wait)


class YTMusicTarget(MirrorTarget):
    name = "YouTube Music"
    tag = "yt"
    source = "ytmusic"

    def __init__(self, auth_file, creds):
        self._auth_file = auth_file
        self._creds = creds
        with open(auth_file) as f:
            self._tok = json.load(f)
        self.cache_file = os.getenv("YTMUSIC_CACHE_FILE", "ytmusic_resolve_cache.json")
        self._session = requests.Session()  # Data API (reads + writes)
        from ytmusicapi import YTMusic
        self._ytm = YTMusic()  # public, unauthenticated search for resolution (no Data API quota)

    # -- auth ------------------------------------------------------------------
    def _access(self):
        """A valid access token, refreshed and persisted when near expiry. The
        refresh token is durable — this is the whole point of the Data API."""
        if time.time() >= self._tok.get("expires_at", 0) - 60:
            fresh = self._creds.refresh_token(self._tok["refresh_token"])
            fresh = fresh if isinstance(fresh, dict) else fresh.as_dict()
            self._tok.update(fresh)
            self._tok["expires_at"] = int(time.time()) + int(fresh.get("expires_in", 3600))
            with open(self._auth_file, "w") as f:
                json.dump(self._tok, f)
        return self._tok["access_token"]

    # -- HTTP (Data API: reads + writes only; search never touches this) --------
    def _request(self, method, path, *, params=None, json_body=None, ok404=False):
        """One Data API call. GET/5xx retry with backoff; 429/409 back off and
        retry (write volume is low now that search is off the Data API); 401 ->
        re-auth; 403 quota -> fail closed for the pass."""
        attempts = 5
        for attempt in range(attempts):
            headers = {"Authorization": f"Bearer {self._access()}"}
            try:
                r = self._session.request(method, f"{API}/{path}", params=params,
                                          json=json_body, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException:
                if method == "GET" and attempt < attempts - 1:
                    time.sleep(min(2 ** attempt, 20) + random.uniform(0, 2))
                    continue
                raise
            if r.status_code == 401:
                raise TargetAuthError("YouTube rejected the OAuth token (401). Re-run the ytmusicapi oauth setup.")
            if r.status_code == 403:
                reason = _err_reason(r)
                if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                    raise TargetAuthError(
                        f"YouTube Data API quota exhausted ({reason}); YT paused until the daily reset (~midnight PT).")
                raise TargetAuthError(f"YouTube refused {method} {path} (403 {reason or 'forbidden'}).")
            if r.status_code == 404 and ok404:
                return None
            if r.status_code in (409, 429) and attempt < attempts - 1:
                # 409 = transient write-conflict on rapid edits; 429 = brief rate
                # blip. The write didn't apply, so a backed-off retry is safe.
                wait = float(r.headers.get("Retry-After") or 0) + min(2 ** attempt, 15) + random.uniform(1, 4)
                time.sleep(wait)
                continue
            if r.status_code >= 500 and method == "GET" and attempt < attempts - 1:
                time.sleep(min(2 ** attempt, 20) + random.uniform(0, 2))
                continue
            r.raise_for_status()
            return r
        return None

    def _paged(self, path, params):
        params = dict(params)
        while True:
            data = self._request("GET", path, params=params).json()
            yield from data.get("items", [])
            token = data.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token

    # -- MirrorTarget ----------------------------------------------------------
    def list_playlists(self):
        out = {}
        for pl in self._paged("playlists", {"part": "snippet,contentDetails", "mine": "true", "maxResults": 50}):
            title = (pl.get("snippet", {}).get("title") or "").strip()
            key = title.casefold()
            if key and key not in out:
                out[key] = {"playlistId": pl["id"], "title": title,
                            "count": pl.get("contentDetails", {}).get("itemCount")}
        return out

    def is_editable(self, playlist):
        return True  # mine=true only returns playlists we own

    def playlist_count(self, playlist):
        return _parse_count(playlist.get("count"))

    def playlist_id(self, playlist):
        return playlist.get("playlistId")

    def playlist_name(self, playlist):
        return playlist.get("title", "")

    def create(self, sp_playlist):
        from .. import spotify

        body = {"snippet": {"title": sp_playlist.get("name", ""), "description": spotify.description(sp_playlist)},
                "status": {"privacyStatus": "private"}}
        pid = self._request("POST", "playlists", params={"part": "snippet,status"}, json_body=body).json()["id"]
        polite_sleep(2.0)  # let the new playlist settle before writing to it
        return {"playlistId": pid, "title": sp_playlist["name"], "count": 0}

    def playlist_tracks(self, playlist):
        tracks = []
        for item in self._paged("playlistItems", {
                "part": "snippet,contentDetails", "playlistId": playlist["playlistId"], "maxResults": 50}):
            vid = item.get("contentDetails", {}).get("videoId")
            if not vid:
                continue
            sn = item.get("snippet", {})
            artist = _artist_from_channel(sn.get("videoOwnerChannelTitle", ""))
            tracks.append({
                "id": vid, "videoId": vid, "playlistItemId": item.get("id"),
                "name": sn.get("title", ""), "artist": artist, "artists": [artist] if artist else [""],
                "album": None, "duration_ms": None,
            })
        return tracks

    def track_id(self, track):
        return track.get("videoId")

    def resolve(self, track, cache):
        primary = track["artists"][0] if track["artists"] else ""
        if not f"{track['name']} {primary}".strip():
            return None, None
        key = track_key(track["name"], " ".join(track["artists"]))
        if key in cache["search"]:
            return cache["search"][key], "search"
        best_id, method = self._search(track, primary)
        cache["search"][key] = best_id
        cache["dirty"] = True
        polite_sleep(0.4)
        return best_id, method

    def _search(self, track, primary):
        """Resolve via ytmusicapi's public search (no Data API quota). Prefer a
        `songs` (art-track) match so tracks land as native songs; fall back to
        `videos` only when no song scores acceptably."""
        queries = [f"{track['name']} {primary}".strip()]
        rom = f"{romanized(track['name'])} {romanized(primary)}".strip()
        if rom and rom != normalize_text(queries[0]):
            queries.append(rom)  # romanized retry for cross-script titles
        for query in queries:
            for filt in ("songs", "videos"):
                try:
                    results = _with_backoff(lambda q=query, f=filt: self._ytm.search(q, filter=f, limit=8),
                                            f"{filt}")
                except Exception:
                    results = []
                best_id, best_score = None, -1.0
                for cand in results or []:
                    vid = cand.get("videoId")
                    if not vid:
                        continue
                    cand_artist = ", ".join(a.get("name", "") for a in cand.get("artists") or []) or cand.get("author") or ""
                    ds = cand.get("duration_seconds")
                    score, ok = score_candidate(track["name"], track["artists"], track["duration_ms"],
                                                cand.get("title", ""), cand_artist, ds * 1000 if ds else None)
                    if ok and score > best_score:
                        best_id, best_score = vid, score
                if best_id:
                    return best_id, ("song" if filt == "songs" else "video")
        return None, None

    def add(self, playlist, target_ids):
        for video_id in target_ids:  # one at a time, in order — append order is date-added order
            self._request("POST", "playlistItems", params={"part": "snippet"}, json_body={
                "snippet": {"playlistId": playlist["playlistId"],
                            "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
            polite_sleep(1.0)

    def remove(self, playlist, track):
        if not track.get("playlistItemId"):
            return  # removal needs the playlist-item id (from playlist_tracks)
        self._request("DELETE", "playlistItems", params={"id": track["playlistItemId"]})
        polite_sleep(1.0)
