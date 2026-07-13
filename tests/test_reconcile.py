"""Offline self-check for the N-way reconcile core + its archive state:
`uv run test_reconcile.py`. Covers the per-provider merge logic (the part that
decides adds vs removes across providers) and the persistence helpers."""

import os
import tempfile

from spotify_mirror.engine import archive
from spotify_mirror.engine.matching import spotify_track_keys
from spotify_mirror.engine.targets.base import _merge, reconcile


# --- merge: the safety-critical set logic (per-provider prev + cur) ----------
def test_steady_state_is_noop():
    prev = {"spotify": {"a", "b"}, "apple": {"a", "b"}}
    cur = {"spotify": {"a", "b"}, "apple": {"a", "b"}}
    _, plan = _merge(prev, cur, set())
    assert all(plan[s] == (set(), set()) for s in plan)


def test_add_propagates():
    prev = {"spotify": {"a"}, "apple": {"a"}}
    cur = {"spotify": {"a", "b"}, "apple": {"a"}}  # b added on spotify
    desired, plan = _merge(prev, cur, set())
    assert desired == {"a", "b"}
    assert plan["spotify"] == (set(), set())        # already has b
    assert plan["apple"] == ({"b"}, set())          # must add b


def test_user_removal_propagates():
    prev = {"spotify": {"a", "t"}, "apple": {"a", "t"}, "ytmusic": {"a", "t"}}
    cur = {"spotify": {"a"}, "apple": {"a", "t"}, "ytmusic": {"a", "t"}}  # user removed t on spotify
    desired, plan = _merge(prev, cur, set())
    assert "t" not in desired
    assert plan["apple"] == (set(), {"t"})          # propagate removal
    assert plan["ytmusic"] == (set(), {"t"})


def test_unmatchable_on_one_provider_is_never_deleted():
    # u lives on spotify + apple but was NEVER matchable on yt (absent from yt's
    # own prev). Its absence from yt must NOT read as a deletion. (The bug that
    # caused this test to exist deleted real tracks across every provider.)
    prev = {"spotify": {"a", "u"}, "apple": {"a", "u"}, "ytmusic": {"a"}}
    cur = {"spotify": {"a", "u"}, "apple": {"a", "u"}, "ytmusic": {"a"}}
    desired, plan = _merge(prev, cur, set())
    assert "u" in desired
    assert plan["spotify"] == (set(), set())        # NOT removed from spotify
    assert plan["apple"] == (set(), set())          # NOT removed from apple
    assert plan["ytmusic"] == ({"u"}, set())        # yt only re-attempts the add (will not_found), never removes


def test_first_pass_only_adds():
    cur = {"spotify": {"a", "b", "c"}, "apple": {"a"}}
    desired, plan = _merge({}, cur, set())          # no stored state yet
    assert desired == {"a", "b", "c"}
    assert plan["apple"] == ({"b", "c"}, set())     # adds only, never removes on first pass


def test_collapsed_provider_is_skipped_no_massdelete():
    prev = {"spotify": {"a", "b", "c", "d"}, "apple": {"a", "b", "c", "d"}}
    cur = {"spotify": {"a", "b", "c", "d"}, "apple": set()}  # apple read collapsed to empty
    desired, plan = _merge(prev, cur, {"apple"})
    assert desired == {"a", "b", "c", "d"}          # apple's emptiness removed nothing
    assert plan["spotify"] == (set(), set())


def test_adds_and_removes_always_disjoint():
    prev = {"spotify": {"a", "b", "c"}, "apple": {"a", "b", "c"}}
    cur = {"spotify": {"a", "b", "x"}, "apple": {"b", "c", "y"}}
    _, plan = _merge(prev, cur, set())
    for src, (add_ids, rem_ids) in plan.items():
        assert not (add_ids & rem_ids), f"{src}: add/remove overlap"


# --- archive: the per-provider persistence helpers ---------------------------
def test_playlist_state_roundtrip_per_source():
    conn = archive.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
    assert archive.get_playlist_state(conn, "aurora", "spotify") == set()
    archive.set_playlist_state(conn, "aurora", "spotify", {"i:A", "i:B"})
    archive.set_playlist_state(conn, "aurora", "apple", {"i:A"})
    assert archive.get_playlist_state(conn, "aurora", "spotify") == {"i:A", "i:B"}
    assert archive.get_playlist_state(conn, "aurora", "apple") == {"i:A"}   # scoped per source
    archive.set_playlist_state(conn, "aurora", "spotify", {"i:A"})          # replaces, not merges
    assert archive.get_playlist_state(conn, "aurora", "spotify") == {"i:A"}
    conn.close()


def test_reverse_links_and_isrcs():
    conn = archive.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
    archive.set_links(conn, "apple", {"sp1": "cat1", "sp2": "cat2"})
    assert archive.get_reverse_links(conn, "apple", ["cat1", "cat2", "catX"]) == {"cat1": "sp1", "cat2": "sp2"}
    archive.upsert_many(conn, "spotify", [
        {"id": "sp1", "isrc": "ISRCA", "name": "A", "artists": ["X"], "duration_ms": 1},
        {"id": "sp2", "isrc": None, "name": "B", "artists": ["Y"], "duration_ms": 1}])
    assert archive.get_isrcs(conn, "spotify", ["sp1", "sp2"]) == {"sp1": "ISRCA"}  # sp2 has no ISRC -> excluded
    conn.close()


def test_dupe_guard_catches_same_song_variant():
    # The exact shape that duplicated Aurora: Spotify lists all artists; Apple
    # shows the primary with the feature in the title. They MUST share a
    # track_key so reconcile's guard skips the add rather than duplicating the
    # song under a second catalog id.
    present = spotify_track_keys({"name": "Drowning (feat. Kodak Black)", "artists": ["BMike"]})
    incoming = spotify_track_keys({"name": "Drowning", "artists": ["BMike", "Kodak Black"]})
    assert incoming & present, "same song across providers must share a key -> guarded against duplicate add"


class _FakePeer:
    """Minimal MirrorTarget for a state-keying test: two peers already holding
    the same ISRC track, so reconcile writes state without any add/remove."""

    def __init__(self, source):
        self.source = self.tag = self.name = source

    def playlist_tracks(self, pl):
        return [{"id": f"{self.source}1", "name": "Song", "artists": ["A"], "artist": "A",
                 "duration_ms": 1000, "isrc": "ISRCX", "added_at": "2020"}]

    def track_id(self, t):
        return t.get("id")

    def prefetch(self, norms, cache):
        pass

    def native_isrc_map(self, cache):
        return {}

    def resolve(self, norm, cache):
        return None, None

    def add(self, pl, ids):
        pass

    def remove(self, pl, raw):
        pass


def test_reconcile_uses_link_key_for_state():
    conn = archive.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
    peers = [_FakePeer("spotify"), _FakePeer("apple")]
    playlists = {"spotify": {"id": "s1"}, "apple": {"id": "a1"}}
    caches = {s: {"isrc": {}, "search": {}, "dirty": False} for s in ("spotify", "apple")}
    reconcile(peers, "Different Display Name", playlists, caches, conn,
              execute=True, max_removals=25, max_adds=200, link_key="LINKED")
    # canonical state persists under the link key, not the display name
    assert archive.get_playlist_state(conn, "LINKED", "spotify") == {"i:ISRCX"}
    assert archive.get_playlist_state(conn, "different display name", "spotify") == set()
    conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print("\nOK: all checks passed")
