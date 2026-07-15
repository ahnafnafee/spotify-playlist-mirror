"""Account connectors: status + the connect entry point per auth kind."""

from songmirror.services.accounts import CONNECTORS
from songmirror.services.accounts.base import DeviceCode
from songmirror.services.settings import SettingsStore


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


def test_ytmusic_enable_disable_browser_mode(tmp_path, monkeypatch):
    # Pasting music.youtube.com headers writes a browser-auth file, validates the
    # cookies with one call, and flips on the no-quota (youtubei) mode; disable reverts.
    import ytmusicapi

    c = _conn("ytmusic", tmp_path)
    monkeypatch.setenv("YTMUSIC_BROWSER_AUTH", str(tmp_path / "browser.json"))

    def fake_setup(filepath=None, headers_raw=None):
        with open(filepath, "w") as f:
            f.write("{}")

    monkeypatch.setattr(ytmusicapi, "setup", fake_setup)
    monkeypatch.setattr("ytmusicapi.YTMusic",
                        lambda *a, **k: type("Y", (), {"get_library_playlists": lambda self, limit=None: []})())

    assert c.enable_browser("Cookie: x").state == "connected"
    assert c._store.get("YTMUSIC_PREFER_BROWSER") == "1"
    assert c.status().detail.startswith("no-quota")  # browser mode surfaces as connected
    assert c.enable_browser("").state == "error"  # empty paste rejected
    c.disable_browser()
    assert c._store.get("YTMUSIC_PREFER_BROWSER") == "0"
