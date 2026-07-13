"""Web layer smoke tests (FastAPI TestClient)."""

from fastapi.testclient import TestClient

from spotify_mirror.settings import SettingsStore
from spotify_mirror.web import create_app


def _app(tmp_path):
    return create_app(settings=SettingsStore(dir=tmp_path))


def test_health(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/health").json() == {"ok": True}


def test_accounts_list_all_unconfigured(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        accounts = client.get("/api/accounts").json()
        assert {a["id"] for a in accounts} == {"spotify", "apple", "ytmusic", "jellyfin"}
        assert all(a["state"] == "unconfigured" for a in accounts)


def test_settings_roundtrip_masks_secrets(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        client.put("/api/settings", json={"SYNC_INTERVAL": "30m", "SPOTIFY_CLIENT_SECRET": "shh"})
        got = client.get("/api/settings").json()
        assert got["SYNC_INTERVAL"] == "30m"
        assert "SPOTIFY_CLIENT_SECRET" not in got  # secret never echoed back


def test_sync_run_queues(tmp_path, monkeypatch):
    import spotify_mirror.sync_service as m

    async def fake(opts):
        return {"ok": True, "per_target": []}

    monkeypatch.setattr(m, "_run_pass_async", fake)
    with TestClient(_app(tmp_path)) as client:
        assert client.post("/api/sync/run?execute=0").status_code == 202


def test_events_route_registered(tmp_path):
    # The live stream itself is verified in the browser E2E; TestClient can't
    # cleanly close an infinite SSE generator, so here we assert wiring + format.
    assert "/events" in _app(tmp_path).openapi()["paths"]


def test_sse_payload_format():
    from spotify_mirror.logs import Event
    from spotify_mirror.web.routers.events import _fmt

    line = _fmt(Event(1.0, "add", "apple", "Song - Artist"))
    assert line.startswith("data: ") and line.endswith("\n\n")
    import json
    payload = json.loads(line[len("data: "):].strip())
    assert payload["kind"] == "add" and payload["tag"] == "apple"
