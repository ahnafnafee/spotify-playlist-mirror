"""Provider playlist accessors (name/id) resolve each service's dict shape."""

from spotify_mirror.engine.targets.apple import AppleMusicTarget
from spotify_mirror.engine.targets.base import MirrorTarget
from spotify_mirror.engine.targets.ytmusic import YTMusicTarget


def test_playlist_name_per_provider_shape():
    # accessors don't use self, so call unbound with a shaped dict
    assert MirrorTarget.playlist_name(None, {"name": "Spot"}) == "Spot"
    assert AppleMusicTarget.playlist_name(None, {"attributes": {"name": "Appl"}}) == "Appl"
    assert YTMusicTarget.playlist_name(None, {"title": "Yt"}) == "Yt"


def test_playlist_id_per_provider_shape():
    assert MirrorTarget.playlist_id(None, {"id": "s1"}) == "s1"          # spotify/apple
    assert YTMusicTarget.playlist_id(None, {"playlistId": "y1"}) == "y1"  # youtube


def test_apple_description_handles_missing():
    assert AppleMusicTarget.playlist_description(None, {"attributes": {}}) == ""
    assert AppleMusicTarget.playlist_description(
        None, {"attributes": {"description": {"standard": "hi"}}}
    ) == "hi"
