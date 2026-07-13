"""SyncService serializes passes and records outcomes."""

import asyncio

from spotify_mirror.services.events import EventBus
from spotify_mirror.services.settings import SettingsStore


def test_run_now_coalesces(monkeypatch, tmp_path):
    calls = []

    async def scenario():
        import spotify_mirror.services.sync_service as m

        async def fake_pass(opts):
            calls.append("start")
            await asyncio.sleep(0.05)
            calls.append("end")
            return {"ok": True, "per_target": []}

        monkeypatch.setattr(m, "_run_pass_async", fake_pass)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        svc = m.SyncService(SettingsStore(dir=tmp_path), bus)
        await asyncio.gather(svc.run_now(False), svc.run_now(False))
        assert svc.status()["last"] == {"ok": True, "per_target": []}
        assert svc.status()["running"] is False

    asyncio.run(scenario())
    assert calls == ["start", "end"]  # the overlapping second call was coalesced


def test_run_now_records_failure(monkeypatch, tmp_path):
    async def scenario():
        import spotify_mirror.services.sync_service as m

        async def boom(opts):
            raise RuntimeError("nope")

        monkeypatch.setattr(m, "_run_pass_async", boom)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        svc = m.SyncService(SettingsStore(dir=tmp_path), bus)
        await svc.run_now(True)
        assert svc.status()["last"]["ok"] is False
        assert svc.status()["running"] is False

    asyncio.run(scenario())
