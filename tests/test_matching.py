"""Offline self-check for identity/matching + archive: `uv run test_matching.py`."""

import os
import tempfile

from songmirror.engine import archive
from songmirror.engine.config import parse_interval
from songmirror.engine.matching import (
    compute_diff, loose_name, normalize_text, protect_removals, romanized,
    score_candidate, track_key,
)
from songmirror.engine.spotify import playlist_item_track

CID = "catalog"


def sp(name, artist, isrc, added, dur=200_000, tid=None):
    return {"id": tid or f"sp:{name}", "isrc": isrc, "name": name, "artists": [artist],
            "duration_ms": dur, "added_at": added}


def ap(name, artist, cid, rel="i.rel"):
    return {"relationship_id": rel, "catalog_id": cid, "name": name, "artist": artist, "duration_ms": 200_000}


def expected(sp_tracks, isrc_candidates=None, links=None):
    """Build {sp_id: set(target_ids)} the way a target would (links + ISRC)."""
    isrc_candidates, links = isrc_candidates or {}, links or {}
    out = {}
    for t in sp_tracks:
        ids = set()
        if links.get(t["id"]):
            ids.add(links[t["id"]])
        for c in isrc_candidates.get(t["isrc"] or "", []):
            if c.get("id"):
                ids.add(c["id"])
        if ids:
            out[t["id"]] = ids
    return out


def cid_of(t):
    return t.get("catalog_id")


def accepts(*a):
    return score_candidate(*a)[1]


def run():
    assert parse_interval("900") == 900 and parse_interval("15m") == 900 and parse_interval("1h") == 3600

    cands = {"ISRC1": [{"id": "cat1"}], "ISRC2": [{"id": "cat2"}], "ISRC3": [{"id": "cat3"}]}

    # ISRC candidate already present -> no add; matched target track -> no remove.
    tracks = [sp("Song A", "Artist", "ISRC1", "2024-01-01T00:00:00Z")]
    to_add, to_remove = compute_diff(tracks, [ap("Song A (Remaster)", "Artist", "cat1")], expected(tracks, cands), cid_of)
    assert to_add == [] and to_remove == []

    # Missing tracks added oldest-first so the newest lands last.
    tracks = [sp("New", "Artist", "ISRC2", "2026-07-01T00:00:00Z"), sp("Old", "Artist", "ISRC3", "2023-01-01T00:00:00Z")]
    to_add, _ = compute_diff(tracks, [], expected(tracks, cands), cid_of)
    assert [t["name"] for t in to_add] == ["Old", "New"]

    # No-identifier (YouTube-style) diff: key match keeps, fuzzy protects, else remove.
    tracks = [sp("Kept Song", "Same Artist", None, "2024-01-01T00:00:00Z")]
    to_add, to_remove = compute_diff(
        tracks,
        [ap("Kept Song (feat. Same Artist)", "Same Artist", "x", rel="i.keep"),
         ap("Totally Different", "Other", "y", rel="i.gone")],
        expected(tracks), cid_of,
    )
    assert to_add == [] and [t["relationship_id"] for t in to_remove] == ["i.gone"]

    # Link is a hard identifier: suppresses add + protects removal despite name mismatch.
    tracks = [sp("Renamed", "Artist", None, "2024-01-01T00:00:00Z", tid="spL")]
    to_add, to_remove = compute_diff(tracks, [ap("Original Title", "Someone", "catL", rel="i.link")],
                                     expected(tracks, links={"spL": "catL"}), cid_of)
    assert to_add == [] and to_remove == []

    # Net-loss guard: a removal resembling an unresolvable Spotify track is held.
    safe, held = protect_removals(
        [ap("Enemy (From Arcane)", "Imagine Dragons", "e", rel="i.held"),
         ap("Gone", "Nobody", "g", rel="i.safe")],
        [sp("Enemy (with JID) - from Arcane", "Imagine Dragons", None, "2024-01-01T00:00:00Z")],
    )
    assert [t["relationship_id"] for t in held] == ["i.held"]
    assert [t["relationship_id"] for t in safe] == ["i.safe"]

    # --- score_candidate ---
    assert accepts("Elegia - 2015 Remaster", "New Order", 293_000, "Elegia", "New Order", 293_500)
    assert not accepts("Runaway - Piano Version", "AURORA", 243_000, "Runaway", "AURORA", 309_000)
    assert accepts("Jeena Sikha De", ["Arijit Singh", "Ved Sharma", "Kunaal Vermaa"], 271_000,
                   "Jeena Sikha De", "Arijit Singh", 271_000)  # multi-artist
    assert accepts("Tri", ["Popeye Bangladesh"], 241_000,
                   "Popeye (Bangladesh) - Tri (ত্রি) Official Music Video", "Popeye Bangladesh", 241_000)  # video
    assert accepts("Neshar Bojha", ["Syed Hassan Samin"], 300_000, "নেশার বোঝা", "Syed Hassan Samin", 300_000)
    assert accepts("Kamin", ["EMIN"], 200_000, "Камин", "EMIN", 201_000)
    assert not accepts("Oniket Prantor", ["Artcell"], 341_000, "Oniket Prantor", "Mehedi H Joy", 134_000)  # cover

    # --- normalization / scripts ---
    assert loose_name("Камин") == "камин"
    assert romanized("Камин") == "kamin" and romanized("ত্রি") == "tri"
    assert track_key("Камин (feat. JONY)", "EMIN & JONY") == track_key("Камин", "EMIN, JONY")
    assert normalize_text("嘘") == "嘘" and normalize_text("炎") != normalize_text("嘘")

    # --- playlist item shapes ---
    assert playlist_item_track({"track": {"type": "track", "name": "L"}})["name"] == "L"
    assert playlist_item_track({"item": {"type": "track", "name": "C"}})["name"] == "C"
    assert playlist_item_track({"item": {"type": "episode"}}) is None
    assert playlist_item_track({"is_local": True, "item": {"type": "track"}}) is None
    assert playlist_item_track({}) is None

    # --- archive: songs / links / state ---
    db = os.path.join(tempfile.mkdtemp(), "songs.db")
    conn = archive.connect(db)
    assert archive.upsert_many(conn, "spotify", [sp("Song A", "Artist", "ISRC1", "2024-01-01T00:00:00Z")]) == 1
    assert archive.upsert_many(conn, "apple", [{"name": "No Id"}]) == 0  # no id -> skipped
    archive.set_links(conn, "apple", {"sp1": "cat1"})
    archive.set_links(conn, "ytmusic", {"sp1": "vid1"})
    assert archive.get_links(conn, "apple", ["sp1", "sp2"]) == {"sp1": "cat1"}
    assert archive.get_links(conn, "ytmusic", ["sp1"]) == {"sp1": "vid1"}
    assert archive.get_state(conn, "aurora", "apple") is None
    archive.set_state(conn, "aurora", "apple", "snapA", 100)
    assert archive.get_state(conn, "aurora", "apple") == ("snapA", 100)
    conn.close()

    print("OK: all checks passed")


if __name__ == "__main__":
    run()
