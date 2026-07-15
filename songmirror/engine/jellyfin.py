"""Optional: set playlist cover art in Jellyfin via its API.

Jellyfin ignores cover files next to an m3u playlist (it auto-tiles the tracks'
embedded art and honours no filename/`#EXTIMG`), so the ONLY way to give a
playlist a real cover is the API. Opt-in with JELLYFIN_URL + JELLYFIN_API_KEY
(create the key in Jellyfin: Dashboard -> API Keys); skipped otherwise. The
playlist must already exist in Jellyfin (scanned from the m3u) to be matched.
"""

import base64
import os
from concurrent.futures import ThreadPoolExecutor

import requests

from .logs import log_download, log_note, log_warn

TAG = "jelly"


def _track_count(url, key, playlist_id):
    """A Jellyfin playlist's audio-track count via TotalRecordCount — ChildCount
    isn't populated for playlists in the list query, so this is a per-playlist
    lookup. Returns None on any failure (count is best-effort)."""
    try:
        r = requests.get(url + "/Items", headers={"X-Emby-Token": key}, timeout=30,
                         params={"ParentId": playlist_id, "Recursive": "true",
                                 "IncludeItemTypes": "Audio", "Limit": 0,
                                 "EnableTotalRecordCount": "true"})
        r.raise_for_status()
        return r.json().get("TotalRecordCount")
    except Exception:
        return None


def push_covers(playlists):
    """Set each matching Jellyfin playlist's Primary image to its Spotify cover
    (matched by name). Never raises out."""
    url, key = os.getenv("JELLYFIN_URL"), os.getenv("JELLYFIN_API_KEY")
    if not (url and key):
        return
    url = url.rstrip("/")
    headers = {"X-Emby-Token": key}
    try:
        uid = os.getenv("JELLYFIN_USER_ID")
        path = f"/Users/{uid}/Items" if uid else "/Items"
        r = requests.get(url + path, headers=headers, timeout=30,
                         params={"IncludeItemTypes": "Playlist", "Recursive": "true", "Fields": "Name"})
        r.raise_for_status()
        by_name = {}
        for item in r.json().get("Items", []):
            by_name.setdefault((item.get("Name") or "").strip().casefold(), item.get("Id"))
        log_note(f"{len(by_name)} playlist(s) visible", tag=TAG)

        img_cache, pushed, missing = {}, 0, []
        for pl in playlists:
            name = pl.get("name", "")
            item_id = by_name.get(name.strip().casefold())
            if not item_id:
                missing.append(name)
                continue
            images = pl.get("images") or []
            img_url = images[0].get("url") if images else None  # Spotify lists largest first
            if not img_url:
                continue
            if img_url not in img_cache:
                ir = requests.get(img_url, timeout=30)
                ir.raise_for_status()
                img_cache[img_url] = ir.content
            # Jellyfin wants the image base64-encoded in the body, with the
            # actual image type as Content-Type (Spotify covers are JPEG).
            pr = requests.post(f"{url}/Items/{item_id}/Images/Primary",
                              headers={**headers, "Content-Type": "image/jpeg"},
                              data=base64.b64encode(img_cache[img_url]), timeout=30)
            if pr.ok:
                pushed += 1
                log_download(f"cover set: {name}", tag=TAG)
            else:
                log_warn(f"cover failed for '{name}': HTTP {pr.status_code}", tag=TAG)
        if missing:
            log_note(f"not yet in Jellyfin (scan the library, then rerun): {', '.join(missing)}", tag=TAG)
        if pushed:
            log_note(f"{pushed} cover(s) set", tag=TAG)
    except Exception as e:
        log_warn(f"cover push failed: {e!r}", tag=TAG)


def list_playlists():
    """[{id, name, count, image}] of playlists on the Jellyfin server, for the
    web Browse view. Read-only — Jellyfin isn't a sync target, so it's not in the
    targets registry; this lists what's there. Empty list if unconfigured/unreachable."""
    url, key = os.getenv("JELLYFIN_URL"), os.getenv("JELLYFIN_API_KEY")
    if not (url and key):
        return []
    url = url.rstrip("/")
    uid = os.getenv("JELLYFIN_USER_ID")
    path = f"/Users/{uid}/Items" if uid else "/Items"
    try:
        r = requests.get(url + path, headers={"X-Emby-Token": key}, timeout=30,
                         params={"IncludeItemTypes": "Playlist", "Recursive": "true"})
        r.raise_for_status()
    except Exception as e:
        log_warn(f"playlist list failed: {e!r}", tag=TAG)
        return []
    rows = []
    for item in r.json().get("Items", []):
        iid, name = item.get("Id"), (item.get("Name") or "").strip()
        if not (iid and name):
            continue
        has_primary = (item.get("ImageTags") or {}).get("Primary")
        # Jellyfin serves item images without a token, so the browser can load
        # this URL directly; omit it when the playlist has no Primary image.
        rows.append({"id": iid, "name": name, "count": None,
                     "image": f"{url}/Items/{iid}/Images/Primary" if has_primary else ""})
    # Counts aren't in the list response, so fetch each concurrently (local
    # server — parallel is cheap; a failed lookup leaves count=None).
    with ThreadPoolExecutor(max_workers=8) as ex:
        counts = list(ex.map(lambda row: _track_count(url, key, row["id"]), rows))
    for row, count in zip(rows, counts):
        row["count"] = count
    return rows
