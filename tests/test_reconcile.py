"""Offline self-check for the N-way reconcile core + its archive state:
`uv run test_reconcile.py`. Covers the per-provider merge logic (the part that
decides adds vs removes across providers) and the persistence helpers."""

import os
import tempfile

from songmirror.engine import archive
from songmirror.engine.matching import spotify_track_keys
from songmirror.engine.targets.base import _merge, reconcile


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


class _P:
    """Reconcile peer with a controllable ISRC set that reflects adds/removes —
    for exercising the persist gate + removal draining across passes."""

    def __init__(self, source, isrcs):
        self.source = self.tag = self.name = source
        self._isrcs = list(isrcs)
        self.removed = []

    def playlist_tracks(self, pl):
        return [{"id": f"{self.source}-{i}", "name": f"Song {i}", "artists": ["A"], "artist": "A",
                 "duration_ms": 1000, "isrc": i, "added_at": "2020"} for i in self._isrcs]

    def track_id(self, t):
        return t.get("id")

    def prefetch(self, norms, cache):
        pass

    def native_isrc_map(self, cache):
        return {}

    def resolve(self, norm, cache):
        return f"{self.source}-{norm['isrc']}", "search"

    def add(self, pl, ids):
        for tid in ids:
            isrc = tid.split("-", 1)[1]
            if isrc not in self._isrcs:
                self._isrcs.append(isrc)

    def remove(self, pl, raw):
        self.removed.append(raw["isrc"])
        if raw["isrc"] in self._isrcs:
            self._isrcs.remove(raw["isrc"])


def _caches(*sources):
    return {s: {"isrc": {}, "search": {}, "dirty": False} for s in sources}


def test_reconcile_saves_baseline_when_only_adds_deferred(tmp_path):
    # The bootstrap fix: a pass that merely DEFERS adds (max_adds hit) is not
    # "clean", yet its per-provider removal baseline must still be recorded — else
    # removals can never activate until the whole add backlog drains.
    conn = archive.connect(str(tmp_path / "s.db"))
    sp, ap = _P("spotify", ["A", "B", "C"]), _P("apple", ["A"])  # apple missing B, C
    stats = reconcile([sp, ap], "Mix", {"spotify": {"id": "s"}, "apple": {"id": "a"}},
                      _caches("spotify", "apple"), conn, execute=True, max_removals=25, max_adds=1)
    assert stats["deferred"] >= 1 and stats["clean"] is False   # add backlog deferred
    assert archive.get_playlist_state(conn, "mix", "spotify") == {"i:A", "i:B", "i:C"}  # baseline still saved
    conn.close()


def test_large_removals_held_back_by_default_then_drain_when_opted_in(tmp_path):
    isrcs = list("ABCDEFGHIJ")

    def fresh():
        conn = archive.connect(str(tmp_path.joinpath(f"s{len(isrcs)}.db")))
        for src in ("spotify", "apple"):
            archive.set_playlist_state(conn, "mix", src, {f"i:{i}" for i in isrcs})
        sp = _P("spotify", ["A", "B", "C", "H", "I", "J"])  # user dropped D,E,F,G (keeps 6/10 -> no collapse)
        ap = _P("apple", list(isrcs))
        return conn, sp, ap

    playlists = {"spotify": {"id": "s"}, "apple": {"id": "a"}}

    # Default: 4 removals > max_removals=2 -> held back entirely, surfaced, baseline frozen.
    conn, sp, ap = fresh()
    stats = reconcile([sp, ap], "Mix", playlists, _caches("spotify", "apple"), conn,
                      execute=True, max_removals=2, max_adds=200, drain_removals=False)
    assert stats["removals_skipped"] == 4 and ap.removed == []
    assert archive.get_playlist_state(conn, "mix", "apple") == {f"i:{i}" for i in isrcs}  # not advanced
    conn.close()

    # Opt-in: drains 2/pass across two passes, advancing the baseline only once cleared.
    conn, sp, ap = fresh()
    reconcile([sp, ap], "Mix", playlists, _caches("spotify", "apple"), conn,
              execute=True, max_removals=2, max_adds=200, drain_removals=True)
    assert len(ap.removed) == 2 and archive.get_playlist_state(conn, "mix", "apple") == {f"i:{i}" for i in isrcs}
    reconcile([sp, ap], "Mix", playlists, _caches("spotify", "apple"), conn,
              execute=True, max_removals=2, max_adds=200, drain_removals=True)
    assert len(ap.removed) == 4  # fully drained
    assert archive.get_playlist_state(conn, "mix", "apple") == {f"i:{i}" for i in ("A", "B", "C", "H", "I", "J")}
    conn.close()


class _VariantPeer:
    """Peer holding ONE copy of a song under provider-flavored metadata
    (decorated title, partial or embellished artist credits). resolve() returns
    a catalog id different from the library id already in the playlist — the
    real-world shape that let re-adds slip past the seen-id guard."""

    def __init__(self, source, track, resolve_id):
        self.source = self.tag = self.name = source
        self._track = track
        self._resolve_id = resolve_id
        self.added, self.removed = [], []

    def playlist_tracks(self, pl):
        return [dict(self._track)]

    def track_id(self, t):
        return t.get("id")

    def prefetch(self, norms, cache):
        pass

    def native_isrc_map(self, cache):
        return {}

    def resolve(self, norm, cache):
        return self._resolve_id, "search"

    def add(self, pl, ids):
        self.added.extend(ids)

    def remove(self, pl, raw):
        self.removed.append(raw)


def _arcane_peers():
    """One song, three provider-flavored copies: Spotify lists every artist plus
    the ISRC; Apple joins the artists into one embellished string; YT credits
    only the primary. Without alias unification each shape becomes its own
    canonical id."""
    name = "To Ashes and Blood (from the series Arcane League of Legends)"
    sp = _VariantPeer("spotify", {"id": "sp-lib", "name": name,
                                  "artists": ["Woodkid", "Arcane", "League of Legends"],
                                  "artist": "Woodkid, Arcane, League of Legends",
                                  "duration_ms": 246000, "isrc": "X1", "added_at": "2020"}, "sp-cat")
    ap = _VariantPeer("apple", {"id": "ap-lib", "name": name,
                                "artist": "Woodkid, Arcane, League of Legends Music",
                                "duration_ms": 246000, "isrc": None, "added_at": "2020"}, "ap-cat")
    yt = _VariantPeer("ytmusic", {"id": "yt-lib", "name": name, "artists": ["Woodkid"],
                                  "artist": "Woodkid", "duration_ms": 246000, "isrc": None,
                                  "added_at": "2020"}, "yt-cat")
    return sp, ap, yt


def test_alias_variants_do_not_duplicate_across_providers(tmp_path):
    # Every provider already HAS the song; a pass must be a no-op. Before alias
    # unification, the k: identities from Apple/YT metadata sat in `desired`
    # and were re-added elsewhere via search — duplicating the song on services
    # that already had it, every pass.
    conn = archive.connect(str(tmp_path / "s.db"))
    sp, ap, yt = _arcane_peers()
    stats = reconcile([sp, ap, yt], "Mix", {p.source: {"id": p.source} for p in (sp, ap, yt)},
                      _caches("spotify", "apple", "ytmusic"), conn,
                      execute=True, max_removals=25, max_adds=200)
    assert stats["added"] == 0 and stats["removed"] == 0
    assert sp.added == [] and ap.added == [] and yt.added == []
    for src in ("spotify", "apple", "ytmusic"):  # baseline stores ONE unified identity, not three
        assert archive.get_playlist_state(conn, "mix", src) == {"i:X1"}
    conn.close()


def test_alias_flip_never_removes_the_real_track(tmp_path):
    # A provider's canonical for a song can FLIP between passes (an ISRC or link
    # appears where only a fuzzy key existed). The retired alias then reads as a
    # user deletion and the very-much-present song gets removed from the other
    # providers. Unification maps the stored alias forward instead.
    conn = archive.connect(str(tmp_path / "s.db"))
    name = "Ma Meilleure Ennemie"
    stale = "k:ma meilleure ennemie|stromae pomme arcane"
    archive.set_playlist_state(conn, "mix", "spotify", {"i:Z9"})
    archive.set_playlist_state(conn, "mix", "apple", {stale})
    archive.set_playlist_state(conn, "mix", "ytmusic", {stale})
    sp = _VariantPeer("spotify", {"id": "sp-lib", "name": name, "artists": ["Stromae", "Pomme"],
                                  "artist": "Stromae, Pomme", "duration_ms": 178000,
                                  "isrc": "Z9", "added_at": "2020"}, "sp-cat")
    ap = _VariantPeer("apple", {"id": "ap-lib", "name": name,
                                "artist": "Stromae, Pomme, Arcane", "duration_ms": 178000,
                                "isrc": None, "added_at": "2020"}, "ap-cat")
    yt = _VariantPeer("ytmusic", {"id": "yt-lib", "name": name, "artists": ["Stromae", "Pomme"],
                                  "artist": "Stromae, Pomme", "duration_ms": 178000,
                                  "isrc": "Z9",  # the flip: yt now reads an ISRC it didn't have
                                  "added_at": "2020"}, "yt-cat")
    stats = reconcile([sp, ap, yt], "Mix", {p.source: {"id": p.source} for p in (sp, ap, yt)},
                      _caches("spotify", "apple", "ytmusic"), conn,
                      execute=True, max_removals=25, max_adds=200)
    assert stats["removed"] == 0 and ap.removed == [] and yt.removed == []
    assert stats["added"] == 0
    conn.close()


def test_unify_uses_every_copys_keys_not_just_the_first():
    # Live-data shape (chai & chill): one identity, two Spotify releases — the
    # decorated title sits FIRST in playlist order. The junk YT copy matches
    # only the plain second copy's keys; unification must consider every
    # entry's keys, not just the first copy folded into canon.
    from songmirror.engine.matching import track_key
    from songmirror.engine.targets.base import _normalize, _unify_aliases

    dec = _normalize({"name": 'Kuch To Hai (From "Do Lafzon Ki Kahani")',
                      "artists": ["Armaan Malik"], "isrc": "I1"}, "spotify")
    plain = _normalize({"name": "Kuch To Hai",
                        "artists": ["Armaan Malik", "Amaal Mallik", "Manoj Muntashir"],
                        "isrc": "I1"}, "spotify")
    junk = _normalize({"name": "KUCH TO HAI", "artists": ["ARMAAN MALIK", "AMAAL MALLIK"]}, "ytmusic")
    kid = f"k:{track_key('KUCH TO HAI', 'ARMAAN MALIK, AMAAL MALLIK')}"
    alias = _unify_aliases({"spotify": [("i:I1", dec), ("i:I1", plain)], "ytmusic": [(kid, junk)]})
    assert alias == {kid: "i:I1"}


def test_unify_folds_ver_abbreviation_into_version():
    # "Twin Ver." vs "Twin Version" — the same release string abbreviated;
    # token-set matching can't bridge ver/version, so loose_name normalizes it.
    from songmirror.engine.matching import track_key
    from songmirror.engine.targets.base import _normalize, _unify_aliases

    sp = _normalize({"name": "Cupid - Twin Ver.", "artists": ["FIFTY FIFTY"], "isrc": "K1"}, "spotify")
    ap = _normalize({"name": "Cupid (Twin Version)", "artist": "FIFTY FIFTY"}, "apple")
    kid = f"k:{track_key('Cupid (Twin Version)', 'FIFTY FIFTY')}"
    alias = _unify_aliases({"spotify": {"i:K1": sp}, "apple": {kid: ap}})
    assert alias == {kid: "i:K1"}


def test_unify_never_merges_different_songs():
    # Same title, different artists (a cover on a label channel) must stay two
    # canonical identities — unification is for provider-flavored metadata of
    # ONE song, never for genuinely different recordings by different artists.
    from songmirror.engine.targets.base import _normalize, _unify_aliases

    orig = _normalize({"name": "Another Day in Paradise", "artists": ["Phil Collins"], "isrc": "P1"}, "spotify")
    cover = _normalize({"name": "Another Day in Paradise",
                        "artists": ["Thriller Records", "Kailee Morgue"]}, "ytmusic")
    alias = _unify_aliases({
        "spotify": {"i:P1": orig},
        "ytmusic": {"k:another day in paradise|thriller records kailee morgue": cover},
    })
    assert alias == {}


def test_unify_folds_reordered_and_embellished_artist_credits():
    # Live-data shape: Spotify credits "Arcane, Woodkid" while Apple credits
    # "Woodkid, Arcane, League of Legends Music" — same song, reordered AND
    # embellished. The composite key's | separator must not block the match by
    # fusing different neighbor tokens together.
    from songmirror.engine.matching import track_key
    from songmirror.engine.targets.base import _normalize, _unify_aliases

    name = "To Ashes and Blood (from the series Arcane League of Legends)"
    sp = _normalize({"name": name, "artist": "Arcane, Woodkid", "isrc": "X1"}, "spotify")
    ap = _normalize({"name": name, "artist": "Woodkid, Arcane, League of Legends Music"}, "apple")
    kid = f"k:{track_key(name, 'Woodkid, Arcane, League of Legends Music')}"
    alias = _unify_aliases({"spotify": {"i:X1": sp}, "apple": {kid: ap}})
    assert alias == {kid: "i:X1"}


def test_order_history_records_dedupes_and_prunes(tmp_path, monkeypatch):
    conn = archive.connect(str(tmp_path / "s.db"))
    stamps = iter(f"2026-01-01T00:00:{i:02d}+00:00" for i in range(60))
    monkeypatch.setattr(archive, "_now", lambda: next(stamps))
    archive.record_order(conn, "mix", "spotify", [["t1", "One", "A"]])
    archive.record_order(conn, "mix", "spotify", [["t1", "One", "A"]])  # unchanged -> no new row
    assert len(archive.get_order_history(conn, "mix", "spotify")) == 1
    for i in range(2, 20):
        archive.record_order(conn, "mix", "spotify", [["t1", "One", "A"]] * i)
    hist = archive.get_order_history(conn, "mix", "spotify")
    assert len(hist) == archive.ORDER_HISTORY_KEEP        # pruned to the retention cap
    assert len(hist[0][1]) == 19                          # newest first, latest snapshot intact
    assert archive.get_order_history(conn, "mix", "apple") == []  # scoped per source
    conn.close()


def test_reconcile_records_order_history(tmp_path):
    conn = archive.connect(str(tmp_path / "s.db"))
    peers = [_FakePeer("spotify"), _FakePeer("apple")]
    reconcile(peers, "Mix", {"spotify": {"id": "s1"}, "apple": {"id": "a1"}},
              _caches("spotify", "apple"), conn, execute=True, max_removals=25, max_adds=200)
    hist = archive.get_order_history(conn, "mix", "spotify")
    assert hist and hist[0][1] == [["spotify1", "Song", "A"]]  # ordered [id, name, artist] rows
    conn.close()


def test_reconcile_interrupt_freezes_baseline(tmp_path):
    # A Pause/Stop mid-reconcile must NOT advance the per-provider baseline — a
    # partial advance could resurrect a track via union_prev on the next pass.
    conn = archive.connect(str(tmp_path / "s.db"))
    sp, ap = _P("spotify", ["A", "B", "C"]), _P("apple", ["A"])
    control = iter(["run"])  # allow the first check, then "stop" (default) interrupts the pass
    reconcile([sp, ap], "Mix", {"spotify": {"id": "s"}, "apple": {"id": "a"}},
              _caches("spotify", "apple"), conn, execute=True, max_removals=25, max_adds=200,
              should_continue=lambda: next(control, "stop"))
    assert archive.get_playlist_state(conn, "mix", "spotify") == set()  # frozen, not advanced
    assert archive.get_playlist_state(conn, "mix", "apple") == set()
    conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print("\nOK: all checks passed")
