"""run_pass returns a per-pass summary dict (consumed by the web layer)."""

import omni_sync.engine.runner as runner
from omni_sync.engine.config import Options


def _opts(**kw):
    base = dict(execute=False, loop=False, interval_s=900, playlists="",
                max_removals=25, max_adds=200, download_dir="", storefront="us",
                cache_file="x", song_cache_file=":memory:")
    base.update(kw)
    return Options(**base)


class _FakeSongs:
    def close(self):
        pass


class _FakeSource:
    """Minimal Spotify-shaped source of truth for run_target."""

    source, name = "spotify", "Spotify"

    def playlist_name(self, pl):
        return pl.get("name", "")

    def playlist_id(self, pl):
        return pl.get("id")


def test_oneway_returns_summary_shape(monkeypatch):
    monkeypatch.setattr(runner.spotify, "client", lambda writable=False: object())
    monkeypatch.setattr(runner.spotify, "playlists_by_name", lambda sp: {})
    monkeypatch.setattr(runner, "build_targets", lambda opts, sp=None: [])
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
        lambda opts, sp, selected, songs, should_continue=None: [runner._summary_entry("N-way", {"added": 3, "removed": 1})],
    )
    s = runner.run_pass(_opts(sync_mode="nway"))
    assert s["mode"] == "nway"
    assert s["per_target"][0]["added"] == 3
    assert s["per_target"][0]["removed"] == 1
    assert s["per_target"][0]["skipped"] == 0  # defaulted keys always present


def test_run_target_honors_explicit_pairing(monkeypatch, tmp_path):
    from omni_sync.engine import archive
    from omni_sync.services.playlists import PlaylistLink

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
                         execute, max_removals, max_adds, drain_removals=False, should_continue=None,
                         source_key="spotify", source_name="Spotify", name=None):
        captured["tgt_id"] = tgt_playlist["id"]
        return {"clean": True, "added": 1, "removed": 0, "missing": 0, "held": 0,
                "deferred": 0, "removals_skipped": 0, "target_count": 1}

    monkeypatch.setattr(runner, "mirror_pair", fake_mirror_pair)

    selected = [{"id": "sp1", "name": "Workout", "snapshot_id": "snap1"}]
    link = PlaylistLink(name="Pair", members={"spotify": "sp1", "apple": "t99"}, id="LINK1")
    agg = runner.run_target(FakeTarget(str(tmp_path / "c.json")), selected, lambda pl: [],
                            songs, _opts(execute=True), links=[link], source=_FakeSource())

    assert captured["tgt_id"] == "t99"          # paired target used, not same-name match
    assert agg["added"] == 1
    assert archive.get_state(songs, "LINK1", "apple") is not None  # state keyed by the link id
    songs.close()


def test_run_target_stops_between_playlists_on_control(tmp_path):
    # The Stop/Pause hook: run_target checks should_continue at each playlist
    # boundary and halts, leaving the rest for a re-run.
    from omni_sync.engine import archive
    from omni_sync.engine.runner import run_target

    songs = archive.connect(str(tmp_path / "s.db"))
    names = []

    class Source:
        source, name = "spotify", "Spotify"

        def playlist_name(self, pl):
            names.append(pl["name"])  # counts playlists whose iteration actually starts
            return pl["name"]

        def playlist_id(self, pl):
            return pl.get("id")

    class Target:
        name, tag, source = "Apple Music", "apple", "apple"
        cache_file = str(tmp_path / "c.json")

        def list_playlists(self):
            return {}  # nothing exists -> dry-run "would create" path, no writes

        def playlist_id(self, pl):
            return pl.get("id")

    control = iter(["run", "stop"])  # process the 1st playlist, stop before the 2nd
    selected = [{"id": "p1", "name": "One"}, {"id": "p2", "name": "Two"}]
    run_target(Target(), selected, lambda pl: [], songs, _opts(),
               source=Source(), should_continue=lambda: next(control, "stop"))
    songs.close()
    assert names == ["One"]  # halted at the playlist boundary, never reached "Two"


def test_mirror_pair_non_spotify_source_never_writes_links(tmp_path):
    # Safety: the archive `links` table is Spotify-anchored and load-bearing for
    # N-way identity, so a non-Spotify one-way source must never write to it —
    # it falls back to track-key matching instead.
    from omni_sync.engine import archive
    from omni_sync.engine.targets.base import mirror_pair

    songs = archive.connect(str(tmp_path / "s.db"))

    class FakeTarget:
        name, tag, source = "YouTube Music", "yt", "ytmusic"
        cache_file = str(tmp_path / "c.json")

        def playlist_tracks(self, pl):
            return []

        def track_id(self, t):
            return t.get("videoId")

        def expected_ids(self, tracks, links, cache):
            return {}

        def prefetch(self, tracks, cache):
            pass

        def resolve(self, track, cache):
            return f"vid_{track['name']}", "search"

        def add(self, pl, ids):
            pass

        def remove(self, pl, t):
            pass

    src = [{"id": "ap1", "name": "Song A", "artists": ["Artist"], "isrc": "US123", "added_at": "2020"}]
    res = mirror_pair(FakeTarget(), src, {"name": "Mix"}, {"id": "p1"}, {}, songs,
                      execute=True, max_removals=25, max_adds=200,
                      source_key="apple", source_name="Apple Music", name="Mix")
    assert res["added"] == 1
    assert songs.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0  # never Spotify-polluted
    songs.close()
