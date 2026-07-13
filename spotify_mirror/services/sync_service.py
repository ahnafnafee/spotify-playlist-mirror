"""SyncService — the single serialization point for every engine invocation.

Owns the schedule and on-demand ("run now") passes. Exactly one pass runs at a
time; a second request while one is in flight is coalesced, because the engine's
on-disk resolve caches and shared SQLite are not safe under concurrent writers.
Passes run in a worker thread so the event loop stays responsive; lifecycle
events reach the live view through the EventBus.
"""

import asyncio
import time

from ..engine import logs
from ..engine.config import DEFAULT_INTERVAL, parse_args, parse_interval
from ..engine.runner import run_pass


async def _run_pass_async(opts):
    """Run one blocking pass off the event loop (patched in tests)."""
    return await asyncio.to_thread(run_pass, opts)


class SyncService:
    def __init__(self, settings, bus):
        self._settings = settings
        self._bus = bus
        self._running = False
        self._running_mode = None  # "preview" | "execute" while a pass runs, else None
        self._task = None
        self._stopping = False
        self._last_summary = None
        self._next_run_at = None  # epoch seconds of the next scheduled pass, or None
        self._lock = asyncio.Lock()  # one engine writer at a time (syncs + transfers)

    async def run_now(self, execute=False):
        # _running is set synchronously right after the check (no await between),
        # so a concurrent caller on the same loop can't slip a second pass in.
        if self._running:
            self._emit("note", "a pass is already running — request coalesced", "sync")
            return
        self._running = True
        self._running_mode = "execute" if execute else "preview"
        try:
            async with self._lock:  # serialize with transfers
                self._settings.apply_to_env()
                opts = parse_args(["--execute"] if execute else [])
                self._emit("section", f"pass started ({'execute' if execute else 'dry run'})", "sync")
                summary = await _run_pass_async(opts)
                self._last_summary = summary
                self._emit("summary", "pass finished", "sync", summary)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # a bad pass must never kill the scheduler
            self._last_summary = {"ok": False, "error": repr(e), "per_target": []}
            self._emit("warn", f"pass failed: {e!r}", "sync")
        finally:
            self._running = False
            self._running_mode = None

    async def run_exclusive(self, fn):
        """Run a blocking engine op (a transfer) serialized with syncs — it queues
        behind any in-flight pass rather than overlapping it."""
        async with self._lock:
            return await asyncio.to_thread(fn)

    async def start(self):
        self._stopping = False
        self._task = asyncio.create_task(self._scheduler())

    async def stop(self):
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _scheduler(self):
        while not self._stopping:
            interval = self._interval_s()
            self._next_run_at = time.time() + interval
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            if not self._stopping:
                await self.run_now(execute=True)
        self._next_run_at = None

    def status(self):
        return {
            "running": self._running,
            "mode": self._running_mode,
            "scheduled": self._task is not None and not self._task.done(),
            "interval_s": self._interval_s(),
            "next_run_at": self._next_run_at,
            "last": self._last_summary,
        }

    def _interval_s(self):
        try:
            return parse_interval(self._settings.get("SYNC_INTERVAL", DEFAULT_INTERVAL))
        except Exception:
            return parse_interval(DEFAULT_INTERVAL)

    def _emit(self, kind, message, tag, data=None):
        self._bus.publish(logs.Event(time.time(), kind, tag, message, data))
