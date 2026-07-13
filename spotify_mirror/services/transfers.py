"""One-off cross-service playlist transfers + conflict review.

An isolated copy engine: it normalizes both sides via the sync core's `_normalize`
and reuses each provider's `resolve`/`add`, so it never touches the safety-critical
`mirror_pair`. Copy mode (adds only) — the safe headline case; mirror-with-removals
is a follow-up.
"""

import asyncio
import time
import uuid

from ..engine import logs, spotify
from ..engine.config import parse_args
from ..engine.logs import log_add, log_miss
from ..engine.matching import spotify_track_keys, track_key
from ..engine.runner import load_cache, save_cache
from ..engine.targets import build_one
from ..engine.targets.base import TargetAuthError, _normalize


def transfer(source, dest, src_pl, dest_pl, cache, *, execute, max_adds):
    """Copy `src_pl` (on `source`) into `dest_pl` (on `dest`). Returns
    {added, deferred, not_found: [{name, artist, key}]}. `not_found` are tracks
    that resolved to nothing on the destination — the conflict queue."""
    src = [_normalize(t, source.source) for t in source.playlist_tracks(src_pl)]
    dst = [_normalize(t, dest.source) for t in dest.playlist_tracks(dest_pl)]
    seen = set().union(*(spotify_track_keys(n) for n in dst)) if dst else set()

    additions, not_found = [], []
    for norm in sorted(src, key=lambda n: n["added_at"]):
        keys = spotify_track_keys(norm)
        if keys & seen:
            continue  # already on the destination
        try:
            tid, _ = dest.resolve(norm, cache)
        except TargetAuthError:
            raise
        except Exception:
            tid = None
        if tid:
            additions.append(tid)
            seen |= keys
            log_add(f"{norm['name']} - {norm['artist']}", dry=not execute, tag="transfer")
        else:
            not_found.append({"name": norm["name"], "artist": norm["artist"],
                              "key": track_key(norm["name"], norm["artist"])})
            log_miss(f"no match: {norm['name']} - {norm['artist']}", tag="transfer")

    deferred = max(0, len(additions) - max_adds)
    additions = additions[:max_adds]
    if execute and additions:
        dest.add(dest_pl, additions)
    return {"added": len(additions), "deferred": deferred, "not_found": not_found}


def _friendly_error(e):
    """Turn a raw provider exception into a message a user can act on. Falls back
    to repr() for anything unrecognized."""
    status = getattr(e, "http_status", None)
    if status == 403:
        return ("The source service blocked reading this playlist (HTTP 403) — it's most "
                "likely owned by another account, or an editorial/auto-generated playlist the "
                "API can't read. Try a playlist you created.")
    if status == 429:
        return "The provider is rate-limiting (HTTP 429). Wait a moment and try again."
    if status == 404:
        return "That playlist no longer exists on the source (HTTP 404)."
    return repr(e)


class TransferService:
    """One-off cross-service copies, serialized with syncs via SyncService. Jobs
    are in-memory and transient."""

    def __init__(self, settings, bus, sync):
        self._settings = settings
        self._bus = bus
        self._sync = sync
        self._jobs = {}

    def submit(self, spec):
        """spec: {source_provider, source_playlist_id, dest_provider,
        dest_playlist_id | None, dest_name}. Returns the job dict (with id)."""
        job = {
            "id": uuid.uuid4().hex[:8], "status": "queued",
            "source": {"provider": spec["source_provider"],
                       "playlist_id": spec["source_playlist_id"], "playlist_name": ""},
            "dest": {"provider": spec["dest_provider"],
                     "playlist_id": spec.get("dest_playlist_id"),
                     "playlist_name": spec.get("dest_name", "")},
            "added": 0, "deferred": 0, "conflicts": [], "error": None,
        }
        self._jobs[job["id"]] = job
        asyncio.create_task(self._run(job, spec))
        return job

    def get(self, job_id):
        return self._jobs.get(job_id)

    def resolve(self, job_id, key, dest_id):
        """Accept a manual match for a conflict — write it to the destination's
        resolution cache so a re-transfer resolves it."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        cache_file = job.get("_dest_cache_file")
        if cache_file:
            cache = load_cache(cache_file)
            cache["search"][key] = dest_id
            cache["dirty"] = True
            save_cache(cache_file, cache)
        for c in job["conflicts"]:
            if c["key"] == key:
                c["resolved"] = True
        return True

    async def _run(self, job, spec):
        job["status"] = "running"
        self._settings.apply_to_env()
        opts = parse_args([])
        src = self._build(spec["source_provider"], opts)
        dst = self._build(spec["dest_provider"], opts)
        if src is None or dst is None:
            job["status"], job["error"] = "error", "source or destination not connected"
            self._emit("warn", f"transfer: {job['error']}", "transfer")
            return
        job["_dest_cache_file"] = dst.cache_file

        def work():
            src_pl = self._find(src, spec["source_playlist_id"])
            if src_pl is None:
                raise RuntimeError("source playlist not found")
            job["source"]["playlist_name"] = src.playlist_name(src_pl)
            dest_pl = self._dest_playlist(dst, src, src_pl, spec)
            job["dest"]["playlist_name"] = dst.playlist_name(dest_pl)
            cache = load_cache(dst.cache_file)
            self._emit("section", f"transfer: {job['source']['playlist_name']} -> {dst.name}", "transfer")
            res = transfer(src, dst, src_pl, dest_pl, cache, execute=True, max_adds=opts.max_adds)
            save_cache(dst.cache_file, cache)
            return res

        try:
            res = await self._sync.run_exclusive(work)
            job["added"], job["deferred"] = res["added"], res["deferred"]
            job["conflicts"] = [{**c, "resolved": False} for c in res["not_found"]]
            job["status"] = "done"
            self._emit("summary", f"transfer done: +{res['added']} ({len(res['not_found'])} unmatched)",
                       "transfer", {"job_id": job["id"]})
        except Exception as e:
            job["status"], job["error"] = "error", _friendly_error(e)
            self._emit("warn", f"transfer failed: {job['error']}", "transfer")

    def _build(self, provider_id, opts):
        sp = None
        if provider_id == "spotify":
            try:
                sp = spotify.client()
            except Exception:
                return None
        return build_one(provider_id, opts, sp)

    def _find(self, provider, playlist_id):
        for pl in provider.list_playlists().values():
            if provider.playlist_id(pl) == playlist_id:
                return pl
        return None

    def _dest_playlist(self, dst, src, src_pl, spec):
        if spec.get("dest_playlist_id"):
            pl = self._find(dst, spec["dest_playlist_id"])
            if pl is None:
                raise RuntimeError("destination playlist not found")
            return pl
        name = spec.get("dest_name") or src.playlist_name(src_pl)
        return dst.create({"name": name, "description": src.playlist_description(src_pl)})

    def _emit(self, kind, message, tag, data=None):
        self._bus.publish(logs.Event(time.time(), kind, tag, message, data))
