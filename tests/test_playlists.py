"""build_one registry helper + PlaylistService."""

from songmirror.engine import targets
from songmirror.engine.config import parse_args


def test_build_one_unknown_returns_none():
    assert targets.build_one("nope", parse_args([])) is None


def test_build_one_known_dispatches(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(targets._REGISTRY, "spotify", lambda o, sp: sentinel)
    assert targets.build_one("spotify", parse_args([])) is sentinel


def test_is_peer_excludes_browse_only():
    # A sync/transfer peer has a MirrorTarget; browse-only Jellyfin does not.
    assert targets.is_peer("spotify") and targets.is_peer("apple") and targets.is_peer("ytmusic")
    assert not targets.is_peer("jellyfin")
    assert not targets.is_peer("bogus")


def test_build_targets_respects_providers(monkeypatch):
    # Deselecting a provider (via opts.providers) excludes it from one-way targets.
    monkeypatch.setitem(targets._REGISTRY, "apple", lambda o, sp: "APPLE")
    monkeypatch.setitem(targets._REGISTRY, "ytmusic", lambda o, sp: "YT")
    opts = parse_args([])
    opts.providers = "spotify,apple"  # ytmusic left out
    assert targets.build_targets(opts) == ["APPLE"]


def test_empty_providers_means_all(monkeypatch):
    # An empty providers list means "every configured provider" (matching the UI +
    # the empty-playlists convention), NOT "none" — so a job saved without touching
    # the Services step still syncs instead of finding zero peers.
    monkeypatch.setitem(targets._REGISTRY, "spotify", lambda o, sp: "SP")
    monkeypatch.setitem(targets._REGISTRY, "apple", lambda o, sp: "APPLE")
    monkeypatch.setitem(targets._REGISTRY, "ytmusic", lambda o, sp: "YT")
    opts = parse_args([])
    opts.providers = ""
    opts.sync_source = "spotify"
    assert targets.build_targets(opts) == ["APPLE", "YT"]                  # all except the source
    assert targets.build_peers(opts, sp="client") == ["SP", "APPLE", "YT"]  # every peer


def test_blank_storefront_defaults(monkeypatch):
    # A blank APPLE_STOREFRONT (saved when the Apple connect leaves it empty) must
    # fall back to the default, not go into the URL and yield /catalog//search (400).
    monkeypatch.setenv("APPLE_STOREFRONT", "")
    assert parse_args([]).storefront == "us"


def test_browse_normalizes_rows(monkeypatch, tmp_path):
    from songmirror.services.playlists import PlaylistService
    from songmirror.services.settings import SettingsStore

    class FakeTarget:
        def list_playlists(self):
            return {"chill": {"id": "1", "name": "Chill", "tracks": {"total": 5}}}

        def browse_playlists(self):
            return list(self.list_playlists().values())

        def playlist_count(self, pl):
            return (pl.get("tracks") or {}).get("total")

    monkeypatch.setattr("songmirror.services.playlists.build_one", lambda pid, opts, sp=None: FakeTarget())
    rows = PlaylistService(SettingsStore(dir=tmp_path)).browse("apple")
    assert rows == [{"id": "1", "name": "Chill", "count": 5, "image": "", "owned": True}]


def test_browse_lists_followed_spotify_playlists(monkeypatch, tmp_path):
    # Spotify browse lists followed (non-owned) playlists alongside owned ones, via
    # the un-deduped all_playlists(), and labels each with `owned` so the UI can
    # divide Created from Followed.
    from songmirror.services.playlists import PlaylistService
    from songmirror.services.settings import SettingsStore

    monkeypatch.setattr("songmirror.services.playlists.spotify.client", lambda *a, **k: object())
    monkeypatch.setattr(
        "songmirror.services.playlists.spotify.all_playlists",
        lambda sp: [{"id": "1", "name": "Mine", "owner": {"id": "me"}, "_owned": True},
                    {"id": "2", "name": "Theirs", "owner": {"id": "other"}, "_owned": False}],
    )
    rows = PlaylistService(SettingsStore(dir=tmp_path)).browse("spotify")
    assert {r["name"]: r["owned"] for r in rows} == {"Mine": True, "Theirs": False}


def test_track_total_reads_both_shapes():
    # Spotify's /me/playlists object moved the count from `tracks.total` to
    # `items.total`; read the current key first, fall back to the legacy one.
    from songmirror.engine.spotify import track_total

    assert track_total({"items": {"total": 212}}) == 212
    assert track_total({"tracks": {"total": 7}}) == 7
    assert track_total({"items": {"total": 3}, "tracks": {"total": 99}}) == 3
    assert track_total({}) is None


def test_pl_image_extraction():
    from songmirror.services.playlists import _pl_image

    assert _pl_image({"images": [{"url": "http://sp/cover.jpg"}]}) == "http://sp/cover.jpg"
    assert _pl_image({"attributes": {"artwork": {"url": "http://ap/{w}x{h}bb.jpg"}}}) == "http://ap/300x300bb.jpg"
    assert _pl_image({"thumbnails": [{"url": "a"}, {"url": "http://yt/big.jpg"}]}) == "http://yt/big.jpg"
    assert _pl_image({"name": "no art"}) == ""


def test_linkstore_roundtrip(tmp_path):
    from songmirror.services.playlists import LinkStore, PlaylistLink

    store = LinkStore(dir=tmp_path)
    link = store.upsert(PlaylistLink(name="My Pair", members={"spotify": "s1", "apple": None}))
    assert link.id  # generated
    got = store.list()
    assert len(got) == 1 and got[0].name == "My Pair"
    store.delete(link.id)
    assert store.list() == []
