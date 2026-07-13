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
