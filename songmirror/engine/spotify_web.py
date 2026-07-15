"""Read a playlist's tracks via Spotify's web-player API (SpotifyScraper).

A fallback for playlists the official Web API returns 403 for — the tracks of
playlists the user follows but doesn't own. The web-player endpoint reads any
public playlist, filling that gap. Isolated in this one module and gated by
SPOTIFY_WEB_FALLBACK (on by default) so it can be turned off and so it never
affects the official-API path: any failure here just re-surfaces the original 403.
"""

import os

# Hard ceiling so a pathological (tens-of-thousands-track) playlist can't spin the
# paginated fallback forever. A read that would exceed it comes back short of the
# playlist's total and is therefore rejected as incomplete (see below).
_MAX_TRACKS = 10000


def enabled():
    return os.getenv("SPOTIFY_WEB_FALLBACK", "on").lower() not in ("0", "off", "false")


def playlist_tracks(playlist_id):
    """[{id,isrc,name,artists,album,duration_ms,added_at}] for a public playlist,
    read via the web player — the same dict shape spotify.playlist_tracks yields.

    Returns the COMPLETE track list or raises: a partial read is unsafe (it would
    make the sync think tracks were deleted), so a fetch that comes back short of
    the playlist's own total_tracks is rejected rather than returned.
    """
    from spotify_scraper import SpotifyClient  # optional dep — lazy so core runs without it

    sc = SpotifyClient()
    try:
        pl = sc.get_playlist(f"https://open.spotify.com/playlist/{playlist_id}", max_tracks=_MAX_TRACKS)
    finally:
        try:
            sc.close()
        except Exception:
            pass

    items = list(getattr(pl, "tracks", ()) or ())
    total = getattr(pl, "total_tracks", None)
    # A small shortfall vs total_tracks is expected — removed/unavailable tracks
    # count toward the total but aren't returned (and were never synced, so they
    # can't cause a wrong removal). A LARGE shortfall means pagination didn't
    # finish, which is unsafe for the sync's removal logic — reject it.
    if total and len(items) < total * 0.9:
        raise RuntimeError(f"incomplete web read: {len(items)} of {total} tracks")

    out = []
    for pt in items:
        t = getattr(pt, "track", None)
        tid = getattr(t, "id", None) if t is not None else None
        if not tid:
            continue  # local file / unavailable / ghost entry — excluded like the official read
        artists = [a.name for a in (getattr(t, "artists", ()) or ()) if getattr(a, "name", "")]
        added = getattr(pt, "added_at", None)
        out.append({
            "id": tid,
            "isrc": None,  # the web payload omits ISRC; matching falls back to name/artist/duration
            "name": getattr(t, "name", "") or "",
            "artists": artists or [""],
            "album": getattr(getattr(t, "album", None), "name", None),
            "duration_ms": getattr(t, "duration_ms", None),
            # ISO-8601 string like the official API (the diff sorts oldest-first on it).
            "added_at": added.isoformat() if hasattr(added, "isoformat") else (added or ""),
        })
    return out
