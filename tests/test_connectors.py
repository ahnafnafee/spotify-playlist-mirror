"""Account connectors: status + the connect entry point per auth kind."""

from spotify_mirror.services.accounts import CONNECTORS
from spotify_mirror.services.accounts.base import DeviceCode
from spotify_mirror.services.settings import SettingsStore


def _conn(cid, tmp_path):
    return CONNECTORS[cid](SettingsStore(dir=tmp_path))


def test_registry_has_all_four():
    assert set(CONNECTORS) == {"spotify", "apple", "ytmusic", "jellyfin"}


def test_apple_unconfigured_then_submit_stores(tmp_path, monkeypatch):
    c = _conn("apple", tmp_path)
    assert c.status().state == "unconfigured"
    monkeypatch.setattr(c, "_validate", lambda: (True, "ok"))
    st = c.submit({"APPLE_BEARER_TOKEN": "b", "APPLE_USER_TOKEN": "u"})
    assert st.state == "connected"
    assert c._store.get("APPLE_USER_TOKEN") == "u"


def test_jellyfin_unconfigured_then_submit(tmp_path, monkeypatch):
    c = _conn("jellyfin", tmp_path)
    assert c.status().state == "unconfigured"
    monkeypatch.setattr(c, "_ping", lambda: (True, ""))
    assert c.submit({"JELLYFIN_URL": "http://x", "JELLYFIN_API_KEY": "k"}).state == "connected"


def test_spotify_begin_redirect_returns_url(tmp_path, monkeypatch):
    c = _conn("spotify", tmp_path)
    assert c.status().state == "unconfigured"

    class FakeOAuth:
        def get_authorize_url(self):
            return "https://accounts.spotify.com/authorize?x=1"

    monkeypatch.setattr(c, "_oauth", lambda redirect_uri: FakeOAuth())
    url = c.begin_redirect("http://host/oauth/spotify/callback")
    assert url.startswith("https://accounts.spotify.com/authorize")
    assert c._store.get("SPOTIFY_REDIRECT_URI") == "http://host/oauth/spotify/callback"


def test_ytmusic_begin_device_surfaces_code(tmp_path, monkeypatch):
    c = _conn("ytmusic", tmp_path)
    assert c.status().state == "unconfigured"

    class FakeCreds:
        def get_code(self):
            return {"user_code": "ABCD-1234", "verification_url": "https://google.com/device",
                    "device_code": "dev123", "interval": 5}

    monkeypatch.setattr(c, "_creds", lambda: FakeCreds())
    dc = c.begin_device()
    assert isinstance(dc, DeviceCode)
    assert dc.user_code == "ABCD-1234"
    assert dc.device_code == "dev123"
