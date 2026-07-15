"""SyncService serializes passes and records outcomes (per named job)."""

import asyncio

from songmirror.services.events import EventBus
from songmirror.services.settings import SettingsStore
from songmirror.services.syncs import SyncJob, SyncStore


def _svc(tmp_path, bus):
    import songmirror.services.sync_service as m

    store = SyncStore(dir=tmp_path)
    job = store.upsert(SyncJob(name="J"))
    return m.SyncService(SettingsStore(dir=tmp_path), bus, store), job.id


def test_run_job_coalesces(monkeypatch, tmp_path):
    calls = []

    async def scenario():
        import songmirror.services.sync_service as m

        async def fake_pass(opts, should_continue=None):
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


def test_pause_marks_job_resumable(monkeypatch, tmp_path):
    # Pause only affects the running job; the interrupted pass is recorded as
    # "paused" so the card can offer Resume.
    async def scenario():
        import songmirror.services.sync_service as m

        started, release = asyncio.Event(), asyncio.Event()

        async def held_pass(opts, should_continue=None):
            started.set()
            await release.wait()
            return {"ok": True, "per_target": [], "interrupted": should_continue()}

        monkeypatch.setattr(m, "_run_pass_async", held_pass)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        svc, jid = _svc(tmp_path, bus)
        task = asyncio.create_task(svc.run_job(jid, True))
        await started.wait()                       # pass is now running
        assert svc.stop("other") is False          # only the running job responds
        assert svc.pause(jid) is True and svc._control == "pause"
        release.set()
        await task
        st = svc.status()
        assert st["last"]["interrupted"] == "pause"
        assert st["jobs"][0]["paused"] is True

    asyncio.run(scenario())


def test_resume_reruns_a_paused_job(monkeypatch, tmp_path):
    runs = []

    async def scenario():
        import songmirror.services.sync_service as m

        async def fake_pass(opts, should_continue=None):
            runs.append(1)
            return {"ok": True, "per_target": [], "interrupted": None}

        monkeypatch.setattr(m, "_run_pass_async", fake_pass)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        svc, jid = _svc(tmp_path, bus)
        assert svc.resume(jid) is False            # not paused -> no-op
        svc._interrupted[jid] = "paused"           # simulate a paused job
        assert svc.resume(jid) is True
        for _ in range(50):
            if runs:
                break
            await asyncio.sleep(0.01)
        assert runs == [1]                         # resume triggered exactly one re-run
        assert svc.status()["jobs"][0]["paused"] is False  # cleared once it re-ran

    asyncio.run(scenario())


def test_run_job_records_failure(monkeypatch, tmp_path):
    async def scenario():
        import songmirror.services.sync_service as m

        async def boom(opts, should_continue=None):
            raise RuntimeError("nope")

        monkeypatch.setattr(m, "_run_pass_async", boom)
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        svc, jid = _svc(tmp_path, bus)
        await svc.run_job(jid, True)
        assert svc.status()["last"]["ok"] is False
        assert svc.status()["running"] is False

    asyncio.run(scenario())
