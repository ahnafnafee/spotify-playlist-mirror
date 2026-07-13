"""Named sync jobs — multiple independent sync configurations (Soundiiz-style).

Each job is a self-contained sync config: a name, on/off, direction, one-way
source of truth, participating providers, playlist filter, safety caps, and its
OWN auto-sync interval. The download mirror stays global (SettingsStore's
DOWNLOAD_DIR/LOCAL_MIRROR_FORMAT) — a job just opts in via `download`.

Persisted to data/syncs.json (owner-only) alongside the other data-dir state.
The engine is unchanged: SyncService builds an Options per job and runs it, so
each job is an ordinary pass.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..engine.config import (
    DEFAULT_INTERVAL, DEFAULT_MAX_ADDS, DEFAULT_MAX_REMOVALS, DEFAULT_PROVIDERS,
    DEFAULT_SYNC_MODE, DEFAULT_SYNC_SOURCE,
)
from .settings import _open_private


@dataclass
class SyncJob:
    name: str = "Sync"
    enabled: bool = True                      # participates in scheduled auto-sync
    mode: str = DEFAULT_SYNC_MODE             # oneway | nway
    source: str = DEFAULT_SYNC_SOURCE         # one-way source of truth
    providers: str = DEFAULT_PROVIDERS        # comma-separated participating providers
    playlists: str = ""                       # comma-separated names (empty = every same-named pair)
    interval: str = DEFAULT_INTERVAL          # this job's own auto-sync cadence
    max_adds: int = DEFAULT_MAX_ADDS
    max_removals: int = DEFAULT_MAX_REMOVALS
    download: bool = False                    # opt into the global download mirror
    id: str = ""


class SyncStore:
    """Named sync jobs persisted to data/syncs.json (owner-only)."""

    def __init__(self, dir="data"):
        self._path = Path(dir) / "syncs.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def list(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                return [SyncJob(**d) for d in json.load(f)]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def get(self, job_id):
        return next((j for j in self.list() if j.id == job_id), None)

    def upsert(self, job):
        if not job.id:
            job.id = uuid.uuid4().hex[:8]
        jobs = [j for j in self.list() if j.id != job.id]
        jobs.append(job)
        self._save(jobs)
        return job

    def delete(self, job_id):
        self._save([j for j in self.list() if j.id != job_id])

    def _save(self, jobs):
        with _open_private(self._path) as f:
            json.dump([asdict(j) for j in jobs], f, indent=2)

    def seed_default(self, settings):
        """Migrate the single global config into one 'Default' job the first time,
        so upgrading from the single-sync model loses nothing. No-op once any job
        exists."""
        if self.list():
            return
        g = settings.load()

        def val(key, default=""):
            # Effective config: settings.json wins, else the process env (the
            # user's .env / docker), matching what the settings API surfaces.
            return g.get(key) or os.getenv(key) or default

        def _int(key, default):
            try:
                return int(val(key, default))
            except (TypeError, ValueError):
                return default

        self.upsert(SyncJob(
            name="Default",
            enabled=settings.get("AUTO_SYNC", "on") != "off",
            mode=val("SYNC_MODE", DEFAULT_SYNC_MODE),
            source=val("SYNC_SOURCE", DEFAULT_SYNC_SOURCE),
            providers=val("PROVIDERS", DEFAULT_PROVIDERS),
            playlists=val("PLAYLISTS"),
            interval=val("SYNC_INTERVAL", DEFAULT_INTERVAL),
            max_adds=_int("MAX_ADDS", DEFAULT_MAX_ADDS),
            max_removals=_int("MAX_REMOVALS", DEFAULT_MAX_REMOVALS),
            download=bool(val("DOWNLOAD_DIR").strip()),
        ))
