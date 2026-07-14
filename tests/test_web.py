"""Web layer smoke tests (FastAPI TestClient)."""

from fastapi.testclient import TestClient

from omni_sync.services.settings import SettingsStore
from omni_sync.services.syncs import SyncStore
from omni_sync.web import create_app


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


def test_settings_falls_back_to_env(tmp_path, monkeypatch):
    # A key absent from settings.json is filled from the process env (a docker
    # env_file / .env), so the UI shows the actual running config.
    monkeypatch.setenv("MAX_ADDS", "321")
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/api/settings").json()["MAX_ADDS"] == "321"


def test_settings_store_uses_data_dir_env(tmp_path, monkeypatch):
    # In Docker, OMNI_DATA_DIR points at the persistent /data volume — the store
    # must write there (not the container-relative ./data default) so wizard
    # config + secrets survive a rebuild.
    vol = tmp_path / "vol"
    monkeypatch.setenv("OMNI_DATA_DIR", str(vol))
    SettingsStore().save({"SPOTIFY_CLIENT_ID": "cid"})
    assert (vol / "settings.json").exists() and (vol / "app.env").exists()
    assert SettingsStore().get("SPOTIFY_CLIENT_ID") == "cid"  # a fresh store reads it back


def test_connector_token_paths_follow_env(tmp_path, monkeypatch):
    # In Docker these env vars point at the /data volume; the connectors must honor
    # them so tokens land on the persistent volume (and where the engine reads
    # them), not a relative ./data that's ephemeral inside the container.
    from omni_sync.services.accounts.spotify import SpotifyConnector
    from omni_sync.services.accounts.ytmusic import YTMusicConnector

    monkeypatch.setenv("SPOTIFY_TOKEN_CACHE", str(tmp_path / "sp_token"))
    monkeypatch.setenv("YTMUSIC_AUTH_FILE", str(tmp_path / "yt.json"))
    store = SettingsStore(dir=tmp_path)
    assert SpotifyConnector(store)._token_cache() == str(tmp_path / "sp_token")
    assert YTMusicConnector(store)._auth_file() == str(tmp_path / "yt.json")


def test_spotify_redirect_uri_forces_loopback_ip(tmp_path):
    # Spotify rejects `localhost` for http loopback redirects — the callback URI
    # must be normalized to the explicit 127.0.0.1 IP no matter how the app is
    # opened. begin_redirect() only builds a URL (no network), so this is offline.
    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "cid", "SPOTIFY_CLIENT_SECRET": "sec"})
    with TestClient(create_app(settings=store), base_url="http://localhost:8080") as client:
        r = client.post("/api/accounts/spotify/connect")
        assert r.status_code == 200
        assert r.json()["redirect_uri"] == "http://127.0.0.1:8080/oauth/spotify/callback"


def test_spotify_redirect_uri_reflects_access_port(tmp_path):
    # Behind Docker the UI is published on a different host port (8888 -> 8080).
    # The redirect URI is derived from the port the BROWSER used (the Host header,
    # which the port-forward preserves), so it must reflect 8888 — that's the URI
    # the connect wizard shows the user to whitelist, and it stays consistent
    # between the authorize step and the token exchange.
    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "cid", "SPOTIFY_CLIENT_SECRET": "sec"})
    with TestClient(create_app(settings=store), base_url="http://localhost:8888") as client:
        assert client.post("/api/accounts/spotify/connect").json()["redirect_uri"] == (
            "http://127.0.0.1:8888/oauth/spotify/callback"
        )
        # begin_redirect persists the exact URI complete_redirect will reuse.
        assert store.get("SPOTIFY_REDIRECT_URI") == "http://127.0.0.1:8888/oauth/spotify/callback"


def test_oauth_callback_handles_provider_error(tmp_path):
    # Spotify (or the user denying) can bounce back with ?error=... instead of a
    # code — the callback must render a friendly page, not a 500 with a raw
    # "Internal Server Error".
    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "cid", "SPOTIFY_CLIENT_SECRET": "sec"})
    with TestClient(create_app(settings=store)) as client:
        r = client.get("/oauth/spotify/callback?error=server_error")
        assert r.status_code == 200
        assert "server_error" in r.text and "Spotify" in r.text


def test_sync_run_queues(tmp_path, monkeypatch):
    import omni_sync.services.sync_service as m

    async def fake(opts):
        return {"ok": True, "per_target": []}

    monkeypatch.setattr(m, "_run_pass_async", fake)
    with TestClient(_app(tmp_path)) as client:
        assert client.post("/api/sync/run?execute=0").status_code == 202


def test_auto_sync_pause_persists_across_restart(tmp_path):
    # Pausing auto-sync must survive a restart — the flag is persisted and the
    # scheduler reads it on boot, so it can't silently turn itself back on.
    store = SettingsStore(dir=tmp_path)
    with TestClient(create_app(settings=store)) as client:
        assert client.get("/api/sync/status").json()["master"] is True
        client.post("/api/sync/schedule", json={"action": "pause"})
        assert client.get("/api/sync/status").json()["master"] is False
    # A fresh app over the same persisted settings dir == a restart.
    with TestClient(create_app(settings=SettingsStore(dir=tmp_path))) as client:
        assert client.get("/api/sync/status").json()["master"] is False


def test_events_route_registered(tmp_path):
    # The live stream itself is verified in the browser E2E; TestClient can't
    # cleanly close an infinite SSE generator, so here we assert wiring + format.
    assert "/events" in _app(tmp_path).openapi()["paths"]


def test_links_crud(tmp_path):
    from omni_sync.services.playlists import LinkStore

    app = create_app(settings=SettingsStore(dir=tmp_path), links=LinkStore(dir=tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/links").json() == []
        lid = client.put("/api/links", json={"name": "Pair", "members": {"spotify": "s1"}}).json()["id"]
        assert lid
        assert len(client.get("/api/links").json()) == 1
        assert client.delete(f"/api/links/{lid}").json() == {"ok": True}
        assert client.get("/api/links").json() == []


def test_syncs_crud(tmp_path):
    # Fresh installs start with NO syncs (no auto-seeded "Default"); jobs are
    # created, merge-updated, and deleted via CRUD.
    store = SyncStore(dir=tmp_path)
    with TestClient(create_app(settings=SettingsStore(dir=tmp_path), syncs=store)) as client:
        assert client.get("/api/syncs").json() == []
        jid = client.post("/api/syncs", json={"name": "Workout", "mode": "oneway", "source": "apple"}).json()["id"]
        assert jid
        client.put(f"/api/syncs/{jid}", json={"enabled": False})
        got = next(j for j in client.get("/api/syncs").json() if j["id"] == jid)
        assert got["enabled"] is False and got["source"] == "apple"  # merge-update kept source
        client.delete(f"/api/syncs/{jid}")
        assert jid not in [j["id"] for j in client.get("/api/syncs").json()]


def test_transfers_start_and_status(tmp_path, monkeypatch):
    from omni_sync.services.transfers import TransferService

    # No providers -> the job errors fast (no network); exercises the REAL submit
    # path (asyncio.create_task) so the async-endpoint requirement can't regress.
    monkeypatch.setattr(TransferService, "_build", lambda self, pid, opts: None)
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/api/transfers", json={"source_provider": "apple", "source_playlist_id": "p1",
                                                "dest_provider": "ytmusic", "dest_playlist_id": "p2"})
        assert r.status_code == 202
        jid = r.json()["job_id"]
        assert jid
        g = client.get(f"/api/transfers/{jid}").json()
        assert g["id"] == jid and "status" in g
        assert "_dest_cache_file" not in g  # internal field hidden from the API


def test_sse_payload_format():
    from omni_sync.engine.logs import Event
    from omni_sync.web.routers.events import _fmt

    line = _fmt(Event(1.0, "add", "apple", "Song - Artist"))
    assert line.startswith("data: ") and line.endswith("\n\n")
    import json
    payload = json.loads(line[len("data: "):].strip())
    assert payload["kind"] == "add" and payload["tag"] == "apple"
