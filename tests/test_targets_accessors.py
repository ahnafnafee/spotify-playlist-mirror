"""Provider playlist accessors (name/id) resolve each service's dict shape."""

from songmirror.engine.targets.apple import AppleMusicTarget
from songmirror.engine.targets.base import MirrorTarget
from songmirror.engine.targets.ytmusic import YTMusicTarget


def test_playlist_name_per_provider_shape():
    # accessors don't use self, so call unbound with a shaped dict
    assert MirrorTarget.playlist_name(None, {"name": "Spot"}) == "Spot"
    assert AppleMusicTarget.playlist_name(None, {"attributes": {"name": "Appl"}}) == "Appl"
    assert YTMusicTarget.playlist_name(None, {"title": "Yt"}) == "Yt"


def test_playlist_id_per_provider_shape():
    assert MirrorTarget.playlist_id(None, {"id": "s1"}) == "s1"          # spotify/apple
    assert YTMusicTarget.playlist_id(None, {"playlistId": "y1"}) == "y1"  # youtube


def test_find_playlist_default_and_spotify_override(monkeypatch):
    # Default scans the name-keyed list_playlists()...
    class T(MirrorTarget):
        def list_playlists(self):
            return {"a": {"id": "1", "name": "A"}, "b": {"id": "2", "name": "B"}}

    t = T()
    assert t.find_playlist("2") == {"id": "2", "name": "B"}
    assert t.find_playlist("9") is None

    # ...but Spotify scans the un-deduped all_playlists, so a followed playlist
    # sharing a name with an owned one is still reachable by id.
    from songmirror.engine.targets.spotify_target import SpotifyTarget

    monkeypatch.setattr("songmirror.engine.spotify.all_playlists",
                        lambda sp: [{"id": "own", "name": "Dup", "_owned": True},
                                    {"id": "flw", "name": "Dup", "_owned": False}])
    target = SpotifyTarget(object(), "cache.json")
    assert target.find_playlist("flw")["id"] == "flw"


def test_apple_description_handles_missing():
    assert AppleMusicTarget.playlist_description(None, {"attributes": {}}) == ""
    assert AppleMusicTarget.playlist_description(
        None, {"attributes": {"description": {"standard": "hi"}}}
    ) == "hi"


def test_ytmusic_browser_backend_maps_shapes_and_is_selected(monkeypatch, tmp_path):
    # The opted-in no-quota browser backend is selected by build(), and maps
    # ytmusicapi's youtubei shapes to the engine's dicts (setVideoId for removal,
    # artists joined, duration in ms; id-less rows dropped).
    import songmirror.engine.targets.ytmusic as yt

    class FakeYTM:
        def __init__(self, *a, **k):
            pass

        def get_playlist(self, pid, limit=None):
            return {"tracks": [
                {"videoId": "v1", "setVideoId": "s1", "title": "Song",
                 "artists": [{"name": "A"}, {"name": "B"}], "album": {"name": "Alb"},
                 "duration_seconds": 200},
                {"videoId": None},
            ]}

        def get_library_playlists(self, limit=None):
            return [{"playlistId": "p1", "title": "Mix", "count": "12 songs",
                     "thumbnails": [{"url": "http://yt/cover.jpg"}]}]

    monkeypatch.setattr("ytmusicapi.YTMusic", FakeYTM)
    auth = tmp_path / "browser.json"
    auth.write_text("{}")
    monkeypatch.setenv("YTMUSIC_BROWSER_AUTH", str(auth))
    monkeypatch.setenv("YTMUSIC_PREFER_BROWSER", "1")

    target = yt.build()
    assert isinstance(target, yt.YTMusicBrowserTarget)

    tracks = target.playlist_tracks({"playlistId": "p1"})
    assert len(tracks) == 1
    t = tracks[0]
    assert (t["videoId"], t["setVideoId"], t["artist"], t["duration_ms"]) == ("v1", "s1", "A, B", 200000)
    assert target.list_playlists() == {
        "mix": {"playlistId": "p1", "title": "Mix", "count": "12 songs",
                "thumbnails": [{"url": "http://yt/cover.jpg"}]}}


def test_apple_playlist_count_uses_meta_total_and_caches():
    # Apple library playlists carry no trackCount, so the count comes from the
    # tracks endpoint's meta.total, cached against lastModifiedDate (one call per
    # playlist, re-fetched only when the playlist changes).
    from songmirror.engine.targets import apple

    apple._COUNT_CACHE.clear()
    target = apple.AppleMusicTarget.__new__(apple.AppleMusicTarget)
    calls = []

    def fake_request(method, url, params=None):
        calls.append(url)
        return type("R", (), {"json": staticmethod(lambda: {"data": [{}], "meta": {"total": 42}})})()

    target._request = fake_request
    pl = {"id": "p1", "attributes": {"lastModifiedDate": "2026-01-01"}}
    assert target.playlist_count(pl) == 42
    assert target.playlist_count(pl) == 42 and len(calls) == 1  # cached, no 2nd call
    changed = {"id": "p1", "attributes": {"lastModifiedDate": "2026-02-01"}}
    assert target.playlist_count(changed) == 42 and len(calls) == 2  # re-fetched on change


def test_jellyfin_list_playlists_fills_counts(monkeypatch):
    # ChildCount isn't populated for playlists in the list query, so counts are
    # a concurrent per-playlist TotalRecordCount lookup.
    from songmirror.engine import jellyfin

    monkeypatch.setenv("JELLYFIN_URL", "http://jf")
    monkeypatch.setenv("JELLYFIN_API_KEY", "k")
    monkeypatch.delenv("JELLYFIN_USER_ID", raising=False)

    def fake_get(url, headers=None, params=None, timeout=None):
        is_list = params.get("IncludeItemTypes") == "Playlist"
        body = {"Items": [{"Id": "p1", "Name": "Mix", "ImageTags": {}}]} if is_list else {"TotalRecordCount": 7}
        return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: body})()

    monkeypatch.setattr(jellyfin.requests, "get", fake_get)
    assert jellyfin.list_playlists() == [{"id": "p1", "name": "Mix", "count": 7, "image": ""}]
