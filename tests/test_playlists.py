"""build_one registry helper + PlaylistService."""

from spotify_mirror.engine import targets
from spotify_mirror.engine.config import parse_args


def test_build_one_unknown_returns_none():
    assert targets.build_one("nope", parse_args([])) is None


def test_build_one_known_dispatches(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(targets._REGISTRY, "spotify", lambda o, sp: sentinel)
    assert targets.build_one("spotify", parse_args([])) is sentinel


def test_browse_normalizes_rows(monkeypatch, tmp_path):
    from spotify_mirror.services.playlists import PlaylistService
    from spotify_mirror.services.settings import SettingsStore

    class FakeTarget:
        def list_playlists(self):
            return {"chill": {"id": "1", "name": "Chill", "tracks": {"total": 5}}}

        def playlist_count(self, pl):
            return (pl.get("tracks") or {}).get("total")

    monkeypatch.setattr("spotify_mirror.services.playlists.build_one", lambda pid, opts, sp=None: FakeTarget())
    rows = PlaylistService(SettingsStore(dir=tmp_path)).browse("apple")
    assert rows == [{"id": "1", "name": "Chill", "count": 5}]


def test_linkstore_roundtrip(tmp_path):
    from spotify_mirror.services.playlists import LinkStore, PlaylistLink

    store = LinkStore(dir=tmp_path)
    link = store.upsert(PlaylistLink(name="My Pair", members={"spotify": "s1", "apple": None}))
    assert link.id  # generated
    got = store.list()
    assert len(got) == 1 and got[0].name == "My Pair"
    store.delete(link.id)
    assert store.list() == []
