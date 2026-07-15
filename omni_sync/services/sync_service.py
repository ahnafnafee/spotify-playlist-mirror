"""SyncService — the single serialization point for every engine invocation.

Runs multiple named sync jobs (SyncStore), each with its own auto-sync interval,
plus on-demand ("run now") passes. Exactly one pass runs at a time — a shared
lock serializes jobs AND transfers, because the engine's on-disk resolve caches
and shared SQLite are not safe under concurrent writers.

Scheduling is per-job: each enabled job gets its own timer, all gated by a global
master switch (AUTO_SYNC — the Dashboard toggle). The download mirror is global
(SettingsStore) and a job opts in via `job.download`. Passes run in a worker
thread so the event loop stays responsive; lifecycle events reach the live view
through the EventBus.
"""

import asyncio
import os
import time

from ..engine import logs
from ..engine.config import DEFAULT_INTERVAL, parse_args, parse_interval
from ..engine.runner import run_pass
from .syncs import SyncStore


async def _run_pass_async(opts):
    """Run one blocking pass off the event loop (patched in tests)."""
    return await asyncio.to_thread(run_pass, opts)


class SyncService:
    def __init__(self, settings, bus, syncs=None):
        self._settings = settings
        self._bus = bus
        self._syncs = syncs or SyncStore()
        self._running_job = None       # id of the job currently holding the lock, or None
        self._running_mode = None      # "preview" | "execute" while a pass runs
        self._active = set()           # ids running-or-queued, to coalesce duplicate triggers
        self._stopping = False
        self._last = {}                # job_id -> last summary
        self._last_any = None          # most recent finished summary (any job), for the dashboard
        self._next_run = {}            # job_id -> epoch seconds of its next scheduled pass
        self._schedulers = {}          # job_id -> asyncio.Task
        self._lock = asyncio.Lock()    # one engine writer at a time (jobs + transfers)

    # -- running ---------------------------------------------------------------
    def _opts_for(self, job, execute):
        """Build engine Options from a job + the global download mirror."""
        self._settings.apply_to_env()
        opts = parse_args([])
        opts.execute = execute
        opts.sync_mode = job.mode
        opts.sync_source = job.source
        opts.providers = job.providers
        opts.playlists = job.playlists
        opts.max_adds = job.max_adds
        opts.max_removals = job.max_removals
        opts.apply_large_removals = job.apply_large_removals
        # OMNI_DOWNLOAD_DIR (a container-internal bind-mount path set by
        # docker-compose) wins over the UI-saved DOWNLOAD_DIR: inside the
        # container that UI value can be a host path (e.g. a Windows F:\ path)
        # that doesn't exist on the container's filesystem, so spotDL would write
        # into the ephemeral container instead of the mounted volume. Unset
        # outside Docker, so the UI value is used there.
        opts.download_dir = (os.getenv("OMNI_DOWNLOAD_DIR") or self._settings.get("DOWNLOAD_DIR", "") or "") if job.download else ""
        return opts

    async def run_job(self, job_id, execute=False):
        """Run one job's pass, serialized on the shared lock. A duplicate trigger
        of a job already running-or-queued is coalesced; a DIFFERENT job queues
        behind the lock rather than overlapping (safe for the engine's caches)."""
        job = self._syncs.get(job_id)
        if job is None:
            return
        if job_id in self._active:
            self._emit("note", f"{job.name}: already running or queued — request coalesced", "sync")
            return
        self._active.add(job_id)
        try:
            async with self._lock:
                self._running_job, self._running_mode = job_id, ("execute" if execute else "preview")
                try:
                    self._emit("section", f"{job.name}: pass started ({'execute' if execute else 'dry run'})", "sync")
                    summary = await _run_pass_async(self._opts_for(job, execute))
                    summary.update(job_id=job_id, job_name=job.name)
                    self._last[job_id] = self._last_any = summary
                    self._emit("summary", f"{job.name}: pass finished", "sync", summary)
                except asyncio.CancelledError:
                    raise
                except BaseException as e:  # a bad pass must never kill the scheduler
                    summary = {"ok": False, "error": repr(e), "per_target": [], "job_id": job_id, "job_name": job.name}
                    self._last[job_id] = self._last_any = summary
                    self._emit("warn", f"{job.name}: pass failed: {e!r}", "sync")
                finally:
                    self._running_job = self._running_mode = None
        finally:
            self._active.discard(job_id)

    async def run_all(self, execute=False):
        """Run every enabled job once, sequentially — the Dashboard's 'Sync now'."""
        for job in self._syncs.list():
            if job.enabled:
                await self.run_job(job.id, execute=execute)

    async def run_exclusive(self, fn):
        """Run a blocking engine op (a transfer) serialized with syncs — it queues
        behind any in-flight pass rather than overlapping it."""
        async with self._lock:
            return await asyncio.to_thread(fn)

    # -- scheduling ------------------------------------------------------------
    def _master_on(self):
        return self._settings.get("AUTO_SYNC", "on") != "off"

    async def start(self):
        self._stopping = False
        await self.reconcile()

    async def reconcile(self):
        """Bring the running per-job schedulers in line with the store + master
        switch. Call after boot and after any job CRUD or master-switch toggle."""
        want = {j.id for j in self._syncs.list() if j.enabled} if (self._master_on() and not self._stopping) else set()
        for jid in list(self._schedulers):
            if jid not in want:
                self._schedulers.pop(jid).cancel()
                self._next_run.pop(jid, None)
        for jid in want:
            if jid not in self._schedulers:
                self._schedulers[jid] = asyncio.create_task(self._job_scheduler(jid))

    async def stop(self):
        self._stopping = True
        for jid in list(self._schedulers):
            self._schedulers.pop(jid).cancel()
        self._next_run.clear()

    async def _job_scheduler(self, job_id):
        try:
            while not self._stopping:
                job = self._syncs.get(job_id)
                if job is None or not job.enabled:
                    break
                interval = self._interval_s(job.interval)
                self._next_run[job_id] = time.time() + interval
                await asyncio.sleep(interval)
                if not self._stopping:
                    await self.run_job(job_id, execute=True)
        except asyncio.CancelledError:
            pass
        finally:
            self._next_run.pop(job_id, None)

    # -- status ----------------------------------------------------------------
    def status(self):
        """Aggregate view (for the dashboard) plus per-job detail."""
        jobs = self._syncs.list()
        next_runs = [self._next_run[j.id] for j in jobs if j.id in self._next_run]
        return {
            "running": self._running_job is not None,
            "mode": self._running_mode,
            "running_job": self._running_job,
            "master": self._master_on(),
            "scheduled": self._master_on() and any(j.enabled for j in jobs),
            "next_run_at": min(next_runs) if next_runs else None,
            "last": self._last_any,
            "jobs": [self._job_status(j) for j in jobs],
        }

    def _job_status(self, job):
        return {
            "id": job.id, "name": job.name, "enabled": job.enabled,
            "running": self._running_job == job.id,
            # Triggered but waiting behind the shared lock for the running pass to
            # finish — passes are serialized (shared caches/archive/rate limits),
            # so a second "Sync now" queues rather than overlaps.
            "queued": job.id in self._active and self._running_job != job.id,
            "next_run_at": self._next_run.get(job.id),
            "last": self._last.get(job.id),
        }

    def _interval_s(self, value):
        try:
            return parse_interval(value)
        except Exception:
            return parse_interval(DEFAULT_INTERVAL)

    def _emit(self, kind, message, tag, data=None):
        self._bus.publish(logs.Event(time.time(), kind, tag, message, data))
