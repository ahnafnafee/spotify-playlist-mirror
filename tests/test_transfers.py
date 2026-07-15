"""Isolated transfer() copy engine + conflict reporting, and TransferService."""

import asyncio

from omni_sync.services.events import EventBus
from omni_sync.services.settings import SettingsStore
from omni_sync.services.sync_service import SyncService
from omni_sync.services.transfers import TransferService, transfer


class _Src:
    source = "spotify"

    def playlist_tracks(self, pl):
        return [
            {"id": "a", "name": "Match", "artists": ["A"], "duration_ms": 1000, "isrc": "I1", "added_at": "2020"},
            {"id": "b", "name": "NoMatch", "artists": ["B"], "duration_ms": 1000, "isrc": "I2", "added_at": "2021"},
            {"id": "c", "name": "Dup", "artists": ["C"], "duration_ms": 1000, "isrc": "I3", "added_at": "2019"},
        ]


def _dst_factory(added):
    class _Dst:
        source = "apple"  # Apple-shaped tracks: singular `artist`, no `artists`

        def playlist_tracks(self, pl):
            return [{"id": "z", "name": "Dup", "artist": "C", "duration_ms": 1000}]

        def resolve(self, norm, cache):
            return ("dest-" + norm["name"], "search") if norm["name"] == "Match" else (None, None)

        def add(self, pl, ids):
            added.extend(ids)

    return _Dst()


def test_transfer_copies_matches_skips_dupes_reports_conflicts():
    added = []
    res = transfer(_Src(), _dst_factory(added), {"id": "s"}, {"id": "d"},
                   {"search": {}, "isrc": {}, "dirty": False}, execute=True, max_adds=100)
    assert res["added"] == 1
    assert added == ["dest-Match"]                          # matchable track added
    assert [c["name"] for c in res["not_found"]] == ["NoMatch"]  # unresolvable -> conflict
    # "Dup" already exists on the destination (same track_key) -> skipped, not re-added


def test_transfer_same_provider_copies_by_id_without_resolving():
    # Spotify -> Spotify (e.g. a followed list into a new owned one): the track's own
    # id is already valid on the destination, so transfer() copies it directly and
    # never invokes the fuzzy resolver.
    added, resolved = [], []

    class _SpotifySrc:
        source = "spotify"

        def playlist_tracks(self, pl):
            return [{"id": "t1", "name": "One", "artists": ["A"], "duration_ms": 1, "added_at": "1"},
                    {"id": "t2", "name": "Two", "artists": ["B"], "duration_ms": 1, "added_at": "2"}]

        def track_id(self, raw):
            return raw["id"]

    class _SpotifyDst:
        source = "spotify"

        def playlist_tracks(self, pl):
            return []

        def resolve(self, norm, cache):
            resolved.append(norm["name"])  # must never be called for same-provider
            return (None, None)

        def add(self, pl, ids):
            added.extend(ids)

    res = transfer(_SpotifySrc(), _SpotifyDst(), {"id": "s"}, {"id": "d"},
                   {"search": {}, "isrc": {}, "dirty": False}, execute=True, max_adds=100)
    assert res["added"] == 2
    assert added == ["t1", "t2"]   # copied by their own ids, oldest-first
    assert resolved == []          # resolver never invoked


def test_transfer_dry_run_adds_nothing():
    added = []
    res = transfer(_Src(), _dst_factory(added), {"id": "s"}, {"id": "d"},
                   {"search": {}, "isrc": {}, "dirty": False}, execute=False, max_adds=100)
    assert res["added"] == 1 and added == []               # counted, but not written


def test_transfer_reports_progress():
    added, calls = [], []
    transfer(_Src(), _dst_factory(added), {"id": "s"}, {"id": "d"},
             {"search": {}, "isrc": {}, "dirty": False}, execute=True, max_adds=100,
             on_progress=lambda p, t, a: calls.append((p, t, a)))
    assert calls[0] == (0, 3, 0)                            # total published before matching
    assert [p for p, _, _ in calls] == [0, 1, 2, 3]        # monotonic scan over all 3 tracks
    assert calls[-1] == (3, 3, 1)                           # every track scanned, 1 added


def test_transfer_stops_early_on_signal():
    added, progress = [], []

    def gate():
        return "stop" if len(progress) >= 2 else "run"  # break before the 3rd track

    res = transfer(_Src(), _dst_factory(added), {"id": "s"}, {"id": "d"},
                   {"search": {}, "isrc": {}, "dirty": False}, execute=True, max_adds=100,
                   on_progress=lambda p, t, a: progress.append(p) if p else None,
                   should_continue=gate)
    assert res["completed"] is False                       # bailed before finishing
    assert res["added"] == 1 and added == ["dest-Match"]   # adds gathered so far still written


def test_transfer_service_control_transitions(tmp_path):
    svc = TransferService(SettingsStore(dir=tmp_path), EventBus(), None)
    svc._jobs = {
        "run1": {"id": "run1", "status": "running", "_control": "run", "_spec": {}},
        "pause1": {"id": "pause1", "status": "paused", "added": 5, "_control": "pause", "_spec": {}},
        "done1": {"id": "done1", "status": "done", "_control": "run"},
    }
    assert {j["id"] for j in svc.list_active()} == {"run1", "pause1"}   # terminal jobs dropped
    assert all("_spec" not in j for j in svc.list_active())            # internals stripped
    assert svc.pause("run1") and svc._jobs["run1"]["_control"] == "pause"
    assert svc.pause("done1") is False                                # only a running job pauses
    assert svc.stop("pause1") and svc._jobs["pause1"]["status"] == "stopped"  # no worker -> mark now
    assert svc.stop("done1") is False                                 # terminal can't be stopped


def test_transfer_service_resume_reruns(monkeypatch, tmp_path):
    async def scenario():
        svc = TransferService(SettingsStore(dir=tmp_path), EventBus(), None)
        ran = []

        async def fake_run(job, spec):
            ran.append((job["id"], spec))

        monkeypatch.setattr(svc, "_run", fake_run)
        svc._jobs["p"] = {"id": "p", "status": "paused", "added": 5,
                          "_control": "pause", "_spec": {"k": 1}}
        assert svc.resume("p") is True
        assert svc._jobs["p"]["status"] == "queued"
        await asyncio.sleep(0)                             # let the scheduled task run
        assert ran == [("p", {"k": 1})]
        assert svc.resume("p") is False                    # already queued, not paused

    asyncio.run(scenario())


def test_run_exclusive_queues_behind_sync(monkeypatch, tmp_path):
    order = []

    async def scenario():
        import omni_sync.services.sync_service as m

        async def fake_pass(opts):
            order.append("sync-start")
            await asyncio.sleep(0.05)
            order.append("sync-end")
            return {"ok": True, "per_target": []}

        monkeypatch.setattr(m, "_run_pass_async", fake_pass)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        from omni_sync.services.syncs import SyncJob, SyncStore

        store = SyncStore(dir=tmp_path)
        job = store.upsert(SyncJob(name="J"))
        sync = SyncService(SettingsStore(dir=tmp_path), bus, store)
        await asyncio.gather(sync.run_job(job.id, False), sync.run_exclusive(lambda: order.append("transfer")))

    asyncio.run(scenario())
    assert order == ["sync-start", "sync-end", "transfer"]  # the transfer waited for the sync


class _Prov:
    def __init__(self, cache_file, tracks, source="apple"):
        self.name, self.source, self.cache_file = "Prov", source, cache_file
        self._tracks = tracks

    def list_playlists(self):
        return {"x": {"id": "p1", "name": "X"}}

    def playlist_id(self, pl):
        return pl.get("id")

    def find_playlist(self, playlist_id):
        return next((pl for pl in self.list_playlists().values() if self.playlist_id(pl) == playlist_id), None)

    def playlist_name(self, pl):
        return pl.get("name", "")

    def playlist_description(self, pl):
        return ""

    def playlist_tracks(self, pl):
        return self._tracks

    def resolve(self, norm, cache):
        return (None, None)  # nothing resolves -> everything becomes a conflict

    def add(self, pl, ids):
        pass

    def create(self, spec):
        return {"id": "new", "name": spec["name"]}


async def _await_job(svc, job_id):
    for _ in range(100):
        if svc.get(job_id)["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.02)
    return svc.get(job_id)


def _service(monkeypatch, tmp_path):
    src = _Prov(str(tmp_path / "s.json"),
                [{"id": "t", "name": "Song", "artists": ["A"], "artist": "A",
                  "duration_ms": 1, "isrc": "I", "added_at": "1"}])
    dst = _Prov(str(tmp_path / "d.json"), [], source="ytmusic")  # empty destination, other provider
    monkeypatch.setattr(TransferService, "_build",
                        lambda self, pid, opts, s=src, d=dst: s if pid == "apple" else d)
    return src, dst


def test_transfer_service_reports_conflicts(monkeypatch, tmp_path):
    out = {}

    async def scenario():
        _service(monkeypatch, tmp_path)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        sync = SyncService(SettingsStore(dir=tmp_path), bus)
        svc = TransferService(SettingsStore(dir=tmp_path), bus, sync)
        job = svc.submit({"source_provider": "apple", "source_playlist_id": "p1",
                          "dest_provider": "ytmusic", "dest_playlist_id": "p1"})
        out["job"] = await _await_job(svc, job["id"])

    asyncio.run(scenario())
    j = out["job"]
    assert j["status"] == "done"
    assert j["added"] == 0
    assert j["total"] == 1 and j["processed"] == 1          # live counters populated via the service
    assert [c["name"] for c in j["conflicts"]] == ["Song"]
    assert j["conflicts"][0]["resolved"] is False


def test_transfer_service_resolve_writes_cache(monkeypatch, tmp_path):
    from omni_sync.engine.runner import load_cache

    out = {}

    async def scenario():
        _, dst = _service(monkeypatch, tmp_path)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        sync = SyncService(SettingsStore(dir=tmp_path), bus)
        svc = TransferService(SettingsStore(dir=tmp_path), bus, sync)
        job = svc.submit({"source_provider": "apple", "source_playlist_id": "p1",
                          "dest_provider": "ytmusic", "dest_playlist_id": "p1"})
        j = await _await_job(svc, job["id"])
        svc.resolve(job["id"], j["conflicts"][0]["key"], "chosen-id")
        out["cache"] = load_cache(dst.cache_file)
        out["job"] = svc.get(job["id"])

    asyncio.run(scenario())
    assert "chosen-id" in out["cache"]["search"].values()  # accepted match cached for next run
    assert out["job"]["conflicts"][0]["resolved"] is True
