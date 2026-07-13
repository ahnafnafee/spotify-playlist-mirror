"""Isolated transfer() copy engine + conflict reporting, and TransferService."""

import asyncio

from spotify_mirror.services.events import EventBus
from spotify_mirror.services.settings import SettingsStore
from spotify_mirror.services.sync_service import SyncService
from spotify_mirror.services.transfers import TransferService, transfer


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


def test_transfer_dry_run_adds_nothing():
    added = []
    res = transfer(_Src(), _dst_factory(added), {"id": "s"}, {"id": "d"},
                   {"search": {}, "isrc": {}, "dirty": False}, execute=False, max_adds=100)
    assert res["added"] == 1 and added == []               # counted, but not written


def test_run_exclusive_queues_behind_sync(monkeypatch, tmp_path):
    order = []

    async def scenario():
        import spotify_mirror.services.sync_service as m

        async def fake_pass(opts):
            order.append("sync-start")
            await asyncio.sleep(0.05)
            order.append("sync-end")
            return {"ok": True, "per_target": []}

        monkeypatch.setattr(m, "_run_pass_async", fake_pass)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        sync = SyncService(SettingsStore(dir=tmp_path), bus)
        await asyncio.gather(sync.run_now(False), sync.run_exclusive(lambda: order.append("transfer")))

    asyncio.run(scenario())
    assert order == ["sync-start", "sync-end", "transfer"]  # the transfer waited for the sync


class _Prov:
    def __init__(self, cache_file, tracks):
        self.name, self.source, self.cache_file = "Prov", "apple", cache_file
        self._tracks = tracks

    def list_playlists(self):
        return {"x": {"id": "p1", "name": "X"}}

    def playlist_id(self, pl):
        return pl.get("id")

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
    dst = _Prov(str(tmp_path / "d.json"), [])  # empty destination
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
    assert [c["name"] for c in j["conflicts"]] == ["Song"]
    assert j["conflicts"][0]["resolved"] is False


def test_transfer_service_resolve_writes_cache(monkeypatch, tmp_path):
    from spotify_mirror.engine.runner import load_cache

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
