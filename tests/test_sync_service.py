"""SyncService serializes passes and records outcomes (per named job)."""

import asyncio

from omni_sync.services.events import EventBus
from omni_sync.services.settings import SettingsStore
from omni_sync.services.syncs import SyncJob, SyncStore


def _svc(tmp_path, bus):
    import omni_sync.services.sync_service as m

    store = SyncStore(dir=tmp_path)
    job = store.upsert(SyncJob(name="J"))
    return m.SyncService(SettingsStore(dir=tmp_path), bus, store), job.id


def test_run_job_coalesces(monkeypatch, tmp_path):
    calls = []

    async def scenario():
        import omni_sync.services.sync_service as m

        async def fake_pass(opts):
            calls.append("start")
            await asyncio.sleep(0.05)
            calls.append("end")
            return {"ok": True, "per_target": []}

        monkeypatch.setattr(m, "_run_pass_async", fake_pass)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        svc, jid = _svc(tmp_path, bus)
        await asyncio.gather(svc.run_job(jid, False), svc.run_job(jid, False))
        assert svc.status()["last"]["ok"] is True
        assert svc.status()["running"] is False

    asyncio.run(scenario())
    assert calls == ["start", "end"]  # the overlapping duplicate trigger was coalesced


def test_run_job_records_failure(monkeypatch, tmp_path):
    async def scenario():
        import omni_sync.services.sync_service as m

        async def boom(opts):
            raise RuntimeError("nope")

        monkeypatch.setattr(m, "_run_pass_async", boom)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        svc, jid = _svc(tmp_path, bus)
        await svc.run_job(jid, True)
        assert svc.status()["last"]["ok"] is False
        assert svc.status()["running"] is False

    asyncio.run(scenario())
