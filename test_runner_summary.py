"""run_pass returns a per-pass summary dict (consumed by the web layer)."""

import spotify_mirror.runner as runner
from spotify_mirror.config import Options


def _opts(**kw):
    base = dict(execute=False, loop=False, interval_s=900, playlists="",
                max_removals=25, max_adds=200, download_dir="", storefront="us",
                cache_file="x", song_cache_file=":memory:")
    base.update(kw)
    return Options(**base)


class _FakeSongs:
    def close(self):
        pass


def test_oneway_returns_summary_shape(monkeypatch):
    monkeypatch.setattr(runner.spotify, "client", lambda writable=False: object())
    monkeypatch.setattr(runner.spotify, "playlists_by_name", lambda sp: {})
    monkeypatch.setattr(runner, "build_targets", lambda opts: [])
    s = runner.run_pass(_opts())
    assert s["mode"] == "oneway"
    assert s["ok"] is True
    assert s["per_target"] == []
    assert isinstance(s["duration_s"], float)


def test_nway_wraps_accumulated_summary(monkeypatch):
    monkeypatch.setattr(runner.spotify, "client", lambda writable=False: object())
    monkeypatch.setattr(runner.spotify, "playlists_by_name", lambda sp: {})
    monkeypatch.setattr(runner.archive, "connect", lambda f: _FakeSongs())
    monkeypatch.setattr(runner, "_post_sync", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "_run_nway",
        lambda opts, sp, selected, songs: [runner._summary_entry("N-way", {"added": 3, "removed": 1})],
    )
    s = runner.run_pass(_opts(sync_mode="nway"))
    assert s["mode"] == "nway"
    assert s["per_target"][0]["added"] == 3
    assert s["per_target"][0]["removed"] == 1
    assert s["per_target"][0]["skipped"] == 0  # defaulted keys always present


def test_run_target_honors_explicit_pairing(monkeypatch, tmp_path):
    from spotify_mirror import archive
    from spotify_mirror.playlists import PlaylistLink

    songs = archive.connect(str(tmp_path / "s.db"))

    class FakeTarget:
        name, tag, source = "Apple Music", "apple", "apple"

        def __init__(self, cache_file):
            self.cache_file = cache_file

        def list_playlists(self):  # a target playlist named differently from the source
            return {"gym music": {"id": "t99", "attributes": {"name": "Gym Music"}}}

        def playlist_id(self, pl):
            return pl.get("id")

        def playlist_count(self, pl):
            return None

        def is_editable(self, pl):
            return True

        def create(self, sp):
            raise AssertionError("must not create; the paired target already exists")

    captured = {}

    def fake_mirror_pair(target, sp_tracks, sp_playlist, tgt_playlist, cache, songs_, *,
                         execute, max_removals, max_adds):
        captured["tgt_id"] = tgt_playlist["id"]
        return {"clean": True, "added": 1, "removed": 0, "missing": 0, "held": 0,
                "deferred": 0, "target_count": 1}

    monkeypatch.setattr(runner, "mirror_pair", fake_mirror_pair)

    selected = [{"id": "sp1", "name": "Workout", "snapshot_id": "snap1"}]
    link = PlaylistLink(name="Pair", members={"spotify": "sp1", "apple": "t99"}, id="LINK1")
    agg = runner.run_target(FakeTarget(str(tmp_path / "c.json")), selected, lambda pid: [],
                            songs, _opts(execute=True), links=[link])

    assert captured["tgt_id"] == "t99"          # paired target used, not same-name match
    assert agg["added"] == 1
    assert archive.get_state(songs, "LINK1", "apple") is not None  # state keyed by the link id
    songs.close()
