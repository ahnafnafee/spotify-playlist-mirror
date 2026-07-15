"""The duplicate-copy cleanup: flag only extra copies of one identity, keep
membership intact, clear the baseline on apply so nothing propagates."""

from omni_sync.engine import archive, dedupe
from omni_sync.engine.targets.base import MirrorTarget, _normalize


class _Peer(MirrorTarget):
    """Fake peer inheriting the default per-entry remove_occurrences."""

    def __init__(self, source, tracks):
        self.source = self.tag = self.name = source
        self._tracks = list(tracks)
        self.removed = []

    def playlist_tracks(self, pl):
        return list(self._tracks)

    def track_id(self, t):
        return t.get("id")

    def remove(self, pl, raw):
        self.removed.append(raw["id"])
        self._tracks = [t for t in self._tracks if t is not raw]  # entry-scoped, like Apple/YT


def _t(id_, name, artist, isrc=None):
    return {"id": id_, "name": name, "artist": artist, "artists": [artist],
            "isrc": isrc, "duration_ms": 1000, "added_at": "2020"}


def _caches(*sources):
    return {s: {"isrc": {}, "search": {}, "dirty": False} for s in sources}


def test_dup_plan_flags_only_extra_copies(tmp_path):
    conn = archive.connect(str(tmp_path / "s.db"))
    sp = _Peer("spotify", [
        _t("sp1", "Song A", "Artist", isrc="A"),
        _t("sp2", "Song B", "Artist", isrc="B"),
        _t("sp3", "Song A", "Artist", isrc="A"),   # re-added under a second catalog id
        _t("sp1", "Song A", "Artist", isrc="A"),   # same-id second copy
    ])
    ap = _Peer("apple", [_t("ap1", "Song A (Official Audio)", "Artist")])  # junk-alias, single copy
    playlists = {"spotify": {"id": "s"}, "apple": {"id": "a"}}
    entries, _ = dedupe.scan([sp, ap], playlists, _caches("spotify", "apple"), conn, "mix")
    plan, held = dedupe.dup_plan([sp, ap], entries)
    assert [(i, raw["id"]) for i, _, raw, _ in plan["spotify"]] == [(2, "sp3"), (3, "sp1")]
    assert plan["apple"] == []  # its one copy unified with Song A, but membership is never touched
    assert held == {"spotify": [], "apple": []}
    conn.close()


def test_apply_removes_clears_state_and_snapshots(tmp_path):
    conn = archive.connect(str(tmp_path / "s.db"))
    archive.set_playlist_state(conn, "mix", "spotify", {"i:A", "i:B"})
    sp = _Peer("spotify", [_t("sp1", "Song A", "Artist", "A"), _t("sp2", "Song B", "Artist", "B"),
                           _t("sp3", "Song A", "Artist", "A")])
    playlists = {"spotify": {"id": "s"}}
    entries, _ = dedupe.scan([sp], playlists, _caches("spotify"), conn, "mix")
    plan, _ = dedupe.dup_plan([sp], entries)
    result = dedupe.apply([sp], playlists, _caches("spotify"), conn, "mix", plan)
    assert sp.removed == ["sp3"]
    assert result["spotify"] == (1, 2)
    assert archive.get_playlist_state(conn, "mix", "spotify") == set()  # baseline cleared, no propagation
    newest = archive.get_order_history(conn, "mix", "spotify")[0][1]
    assert [row[0] for row in newest] == ["sp1", "sp2"]                 # post-clean snapshot on top
    conn.close()


def test_unidentifiable_entries_never_flagged(tmp_path):
    # Empty metadata would collide on an empty key; a missing track id can't be
    # removed. Neither may ever be flagged as a "duplicate".
    conn = archive.connect(str(tmp_path / "s.db"))
    sp = _Peer("spotify", [_t("x1", "", ""), _t("x2", "", ""),
                           _t(None, "Song", "A"), _t(None, "Song", "A")])
    entries, _ = dedupe.scan([sp], {"spotify": {"id": "s"}}, _caches("spotify"), conn, "mix")
    plan, held = dedupe.dup_plan([sp], entries)
    assert plan["spotify"] == [] and held["spotify"] == []
    conn.close()


def test_version_boundary_folds_are_held_not_removed(tmp_path):
    # A live video (fuzzy key, no ISRC) folds into the studio identity so the
    # sync stays quiet — but the studio copy must NEVER be deleted as its
    # "duplicate": the version markers differ, so it lands in `held`.
    conn = archive.connect(str(tmp_path / "s.db"))
    yt = _Peer("ytmusic", [_t("v-live", "American Pie (Live)", "Don McLean"),
                           _t("v-studio", "American Pie", "Don McLean", isrc="S1")])
    entries, _ = dedupe.scan([yt], {"ytmusic": {"id": "y"}}, _caches("ytmusic"), conn, "mix")
    plan, held = dedupe.dup_plan([yt], entries)
    assert plan["ytmusic"] == []
    assert [raw["id"] for _, _, raw, _ in held["ytmusic"]] == ["v-studio"]
    # …while a same-recording decoration ("Official Audio") still dedupes:
    yt2 = _Peer("ytmusic", [_t("v1", "stevie (Official Audio)", "Kasabian"),
                            _t("v2", "stevie", "Kasabian", isrc="K1")])
    entries, _ = dedupe.scan([yt2], {"ytmusic": {"id": "y"}}, _caches("ytmusic"), conn, "mix2")
    plan, held = dedupe.dup_plan([yt2], entries)
    assert [raw["id"] for _, _, raw, _ in plan["ytmusic"]] == ["v2"]
    assert held["ytmusic"] == []
    conn.close()


def test_apple_remove_occurrences_reappends_shared_id_keeper(monkeypatch):
    # Apple's tracks-DELETE takes every copy of a library song with it. When
    # the keeper shares the flagged copy's id it must be re-appended; a group
    # whose copies are all flagged must not be.
    from omni_sync.engine.targets.apple import AppleMusicTarget

    ap = AppleMusicTarget.__new__(AppleMusicTarget)  # no tokens needed for this path
    tracks = [
        {"relationship_id": "L1", "catalog_id": "c1", "name": "Song A", "artist": "X"},  # keeper
        {"relationship_id": "L1", "catalog_id": "c1", "name": "Song A", "artist": "X"},  # dup (same lib id)
        {"relationship_id": "L2", "catalog_id": "c2", "name": "Song B", "artist": "X"},  # dup of a distinct-id keeper
    ]
    removed, added = [], []
    monkeypatch.setattr(ap, "playlist_tracks", lambda pl: list(tracks))
    monkeypatch.setattr(ap, "remove", lambda pl, t: removed.append(t["relationship_id"]))
    monkeypatch.setattr(ap, "add", lambda pl, ids: added.extend(ids))
    ap.remove_occurrences({"id": "p"}, [(1, tracks[1]), (2, tracks[2])])
    assert removed == ["L1", "L2"]
    assert added == ["c1"]  # L1's keeper re-appended; L2 had no unflagged copy in the group


def test_variant_pairs_reports_hard_twins_not_mirrored_ones():
    studio = _normalize({"name": "American Pie", "artist": "Don McLean", "isrc": "S1"}, "spotify")
    live = _normalize({"name": "American Pie - Live", "artist": "Don McLean", "isrc": "L1"}, "spotify")
    canon = {"spotify": {"i:S1": studio, "i:L1": live}, "apple": {"i:S1": studio}}
    assert [(a, b) for a, b, _, _ in dedupe.variant_pairs(canon)] == [("i:L1", "i:S1")]
    canon["apple"]["i:L1"] = live  # now fully mirrored on every provider -> not debris
    assert dedupe.variant_pairs(canon) == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    import tempfile
    from pathlib import Path
    for t in tests:
        t(Path(tempfile.mkdtemp())) if t.__code__.co_argcount else t()
        print(f"ok  {t.__name__}")
    print("\nOK: all checks passed")
