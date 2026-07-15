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
from ..engine.targets import build_one, is_peer
from ..engine.targets.base import TargetAuthError, _normalize


def transfer(source, dest, src_pl, dest_pl, cache, *, execute, max_adds, on_progress=None, should_continue=None):
    """Copy `src_pl` (on `source`) into `dest_pl` (on `dest`). Returns
    {added, deferred, not_found: [{name, artist, key}], completed}. `not_found`
    are tracks that resolved to nothing on the destination — the conflict queue.

    `on_progress(processed, total, added)` (optional) fires after each source
    track is examined, so a caller can surface live progress against the total.

    `should_continue()` (optional) returns "run" | "pause" | "stop"; it's checked
    before each track and, on anything but "run", ends the copy early with
    `completed=False`. Adds gathered before the break are still written, so a
    later re-run skips them (its dedup rebuilds from the destination) and resumes.
    """
    src = [_normalize(t, source.source) for t in source.playlist_tracks(src_pl)]
    dst = [_normalize(t, dest.source) for t in dest.playlist_tracks(dest_pl)]
    seen = set().union(*(spotify_track_keys(n) for n in dst)) if dst else set()

    total = len(src)
    if on_progress:
        on_progress(0, total, 0)  # publish the total once source is read, before matching begins
    # Same-provider copy (e.g. a followed Spotify list into a new owned one): the
    # track's own id is already valid on the destination, so use it directly instead
    # of re-searching for it (which resolve() does for the cross-provider case).
    same_provider = source.source == dest.source
    additions, not_found = [], []
    completed = True
    for i, norm in enumerate(sorted(src, key=lambda n: n["added_at"]), 1):
        if should_continue and should_continue() != "run":
            completed = False  # paused or stopped — leave the rest for a re-run
            break
        keys = spotify_track_keys(norm)
        if not keys & seen:  # skip tracks already on the destination
            if same_provider:
                tid = source.track_id(norm["_raw"])
            else:
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
        if on_progress:
            on_progress(i, total, len(additions))

    deferred = max(0, len(additions) - max_adds)
    additions = additions[:max_adds]
    if execute and additions:
        dest.add(dest_pl, additions)
    return {"added": len(additions), "deferred": deferred, "not_found": not_found, "completed": completed}


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
            "total": 0, "processed": 0,  # live progress: source tracks examined / total
            "_spec": spec,      # kept so resume can re-run the same copy
            "_control": "run",  # "run" | "pause" | "stop" — polled by the running loop
        }
        self._jobs[job["id"]] = job
        asyncio.create_task(self._run(job, spec))
        return job

    def get(self, job_id):
        return self._jobs.get(job_id)

    @staticmethod
    def public(job):
        """A job dict without its internal (_-prefixed) fields — the API shape."""
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def list_active(self):
        """Active jobs (queued/running/paused) for the dashboard. Terminal jobs
        (done/stopped/error) are dropped so the in-memory list can't grow without
        bound in any view — the Transfers page still fetches its own job by id."""
        active = {"queued", "running", "paused"}
        return [self.public(j) for j in self._jobs.values() if j["status"] in active]

    def pause(self, job_id):
        """Ask a running transfer to stop at the next track and hold as `paused`.
        The worker thread returns, freeing the shared engine for scheduled syncs."""
        job = self._jobs.get(job_id)
        if not job or job["status"] != "running":
            return False
        job["_control"] = "pause"
        return True

    def resume(self, job_id):
        """Re-run a paused transfer from its stored spec; dedup skips what's
        already on the destination, so it continues where it left off."""
        job = self._jobs.get(job_id)
        if not job or job["status"] != "paused":
            return False
        job["_control"] = "run"
        job["status"] = "queued"
        asyncio.create_task(self._run(job, job["_spec"]))
        return True

    def stop(self, job_id):
        """Abort a transfer for good. Adds already written stay on the destination
        (stop means 'add no more', not 'undo')."""
        job = self._jobs.get(job_id)
        if not job or job["status"] in ("done", "error", "stopped"):
            return False
        job["_control"] = "stop"
        if job["status"] in ("queued", "paused"):
            job["status"] = "stopped"  # no running worker will react — mark it now
        return True

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
        if job.get("_control") == "stop":  # stopped while still queued — never start
            job["status"] = "stopped"
            return
        job["_control"] = "run"
        job["status"] = "running"
        self._settings.apply_to_env()
        opts = parse_args([])
        for side, prov in (("source", spec["source_provider"]), ("destination", spec["dest_provider"])):
            if not is_peer(prov):  # e.g. Jellyfin — browse-only, can't read/write tracks
                job["status"], job["error"] = "error", f"'{prov}' can't be a transfer {side} — it's a browse-only service."
                self._emit("warn", f"transfer: {job['error']}", "transfer")
                return
        src = self._build(spec["source_provider"], opts)
        dst = self._build(spec["dest_provider"], opts)
        if src is None or dst is None:
            job["status"], job["error"] = "error", "source or destination not connected"
            self._emit("warn", f"transfer: {job['error']}", "transfer")
            return
        job["_dest_cache_file"] = dst.cache_file
        # Adds already on the destination from an earlier (paused) run — this
        # run's transfer() skips them via dedup, so keep the counter cumulative.
        base_added = job["added"]

        def work():
            src_pl = self._find(src, spec["source_playlist_id"])
            if src_pl is None:
                raise RuntimeError("source playlist not found")
            job["source"]["playlist_name"] = src.playlist_name(src_pl)
            dest_pl = self._dest_playlist(dst, src, src_pl, spec)
            job["dest"]["playlist_name"] = dst.playlist_name(dest_pl)
            cache = load_cache(dst.cache_file)
            self._emit("section", f"transfer: {job['source']['playlist_name']} -> {dst.name}", "transfer")

            def on_progress(processed, total, added):
                job["processed"], job["total"], job["added"] = processed, total, base_added + added

            res = transfer(src, dst, src_pl, dest_pl, cache, execute=True, max_adds=opts.max_adds,
                           on_progress=on_progress, should_continue=lambda: job.get("_control", "run"))
            save_cache(dst.cache_file, cache)
            return res

        try:
            res = await self._sync.run_exclusive(work)
            job["added"] = base_added + res["added"]
            job["deferred"] = res["deferred"]
            job["conflicts"] = [{**c, "resolved": False} for c in res["not_found"]]
            if res["completed"]:
                job["status"] = "done"
                self._emit("summary", f"transfer done: +{job['added']} ({len(res['not_found'])} unmatched)",
                           "transfer", {"job_id": job["id"]})
            else:
                job["status"] = "stopped" if job.get("_control") == "stop" else "paused"
                self._emit("note", f"transfer {job['status']}: +{job['added']} so far", "transfer")
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
        # find_playlist scans the provider's full set — for Spotify that's the
        # un-deduped list, so a followed playlist is reachable by id even when a
        # same-named owned one exists (list_playlists() would have hidden it).
        return provider.find_playlist(playlist_id)

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
