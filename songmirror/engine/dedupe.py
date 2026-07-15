"""One-shot cleanup of duplicate playlist copies left by identity splits.

Runs the reconciler's own identity pipeline (per-entry canonical ids, key2isrc
seeding, alias unification) over each provider's current read, then flags every
physical entry whose unified identity repeats an earlier entry's. Removing
those copies never changes canonical membership — at least one copy of each
identity stays on each provider it was on — so the cleanup is invisible to the
N-way merge and can never cascade a removal to another provider.

Separately, `variant_pairs` REPORTS (never removes) hard-id twins: two distinct
recordings of what fuzzy-matching says is one song (live / instrumental /
re-release vs the original — the residue a mis-resolved search add leaves).
Removing one of those IS a membership change, so it stays a human decision.

CLI (report by default; inside the container prefix with `docker exec`):
    uv run python -m songmirror.engine.dedupe [--playlists a,b] [--variants]
    uv run python -m songmirror.engine.dedupe --execute
"""

from . import archive
from .matching import fuzzy_in, loose_name, romanized, spotify_track_keys, track_key
from .targets.base import _entry_cids, _unify_aliases

# Words that mark a DIFFERENT RECORDING of the same song. Alias unification is
# deliberately version-blind (folding "Song (Live)" into "Song" keeps the sync
# from re-propagating provider-local variants), but a DELETION across such a
# boundary would destroy a real recording — e.g. a studio track flagged as a
# "copy" of a live video that happened to fold into it.
VERSION_MARKERS = frozenset((
    "live", "instrumental", "acoustic", "unplugged", "remix", "mix", "karaoke",
    "demo", "cover", "sped", "slowed", "nightcore", "reverb", "orchestral",
    "stripped", "remaster", "remastered", "edit", "version", "ver", "mono", "8d",
))


def _markers(name):
    return VERSION_MARKERS.intersection(loose_name(name).split())


def _order_rows(peer, raw):
    return [[peer.track_id(t), t.get("name", ""),
             t.get("artist") or ", ".join(t.get("artists") or [])] for t in raw]


def scan(peers, playlists, caches, songs, state_key):
    """Read every peer once and unify identities across them.

    Returns (entries, canon): entries = {source: [(unified_cid, raw, norm)]} in
    playlist order; canon = {source: {unified_cid: norm}}. Also snapshots each
    provider's current order into the archive (the pre-cleanup record)."""
    key2isrc, per_entry, canon, raws = {}, {}, {}, {}
    for p in peers:
        raw = p.playlist_tracks(playlists[p.source])
        raws[p.source] = raw
        archive.record_order(songs, state_key, p.source, _order_rows(p, raw))
        ec = _entry_cids(p, raw, songs, caches[p.source], key2isrc)
        per_entry[p.source] = ec
        fold = {}
        for cid, norm in ec:
            fold.setdefault(cid, norm)
        canon[p.source] = fold
        for cid, norm in ec:
            if cid.startswith("i:"):
                key2isrc.setdefault(track_key(norm["name"], norm["artist"]), cid[2:])
    alias = _unify_aliases(per_entry)  # every copy's keys, not just the first per identity
    entries = {}
    for p in peers:
        src = p.source
        entries[src] = [(alias.get(cid, cid), cid, raw_t, norm)
                        for (cid, norm), raw_t in zip(per_entry[src], raws[src])]
        merged = {}
        for cid, norm in canon[src].items():
            merged.setdefault(alias.get(cid, cid), norm)
        canon[src] = merged
    return entries, canon


def dup_plan(peers, entries):
    """(plan, held): plan = {source: [(index, unified_cid, raw, norm)]} — every
    physical copy after the first of its identity, in playlist order (first
    copy = oldest add, so the original stays and the re-added copy goes).

    A later copy is removable only when it is provably the SAME RECORDING as
    the kept one: identical pre-unification identity, or matching version
    markers (a copy that only fuzzy-folded into its keeper across a
    live/remix/... boundary lands in `held` instead — report, never remove).
    Entries with no usable track id, or with neither a name nor an artist
    (unidentifiable reads that would collide on an empty key), are never
    flagged."""
    plan, held = {}, {}
    for p in peers:
        seen, dups, kept = {}, [], []
        for idx, (cid, pre, raw, norm) in enumerate(entries[p.source]):
            if not p.track_id(raw) or not (norm["name"] or norm["artist"]):
                continue
            if cid not in seen:
                seen[cid] = (pre, norm)
                continue
            keeper_pre, keeper_norm = seen[cid]
            if pre == keeper_pre or _markers(norm["name"]) == _markers(keeper_norm["name"]):
                dups.append((idx, cid, raw, norm))
            else:
                kept.append((idx, cid, raw, norm))
        plan[p.source], held[p.source] = dups, kept
    return plan, held


def apply(peers, playlists, caches, songs, state_key, plan):
    """Execute a dup_plan: remove the flagged copies, clear the playlist's
    stored N-way baseline (so the next pass re-bootstraps from the cleaned
    reality instead of reading the removals as user deletions), then re-read
    and snapshot each provider. Returns {source: (removed, count_after)}."""
    out = {}
    for p in peers:
        dups = plan.get(p.source) or []
        if dups:
            p.remove_occurrences(playlists[p.source], [(i, raw) for i, _, raw, _ in dups])
        out[p.source] = [len(dups), None]
    if any(n for n, _ in out.values()):
        archive.clear_playlist_state(songs, state_key)
    for p in peers:
        raw = p.playlist_tracks(playlists[p.source])
        archive.record_order(songs, state_key, p.source, _order_rows(p, raw))
        out[p.source][1] = len(raw)
    return {src: tuple(v) for src, v in out.items()}


def variant_pairs(canon):
    """[(cid_a, cid_b, srcs_a, srcs_b)] — pairs of DISTINCT hard identities that
    fuzzy-match as the same song, where at least one side is missing from some
    provider (twins fully mirrored everywhere are a long-standing choice, not
    debris). Report-only: which recording belongs is a human call.

    Candidates are bucketed by the title's first token before the quadratic
    fuzzy comparison — variants share their title head, and full pairwise over
    a 1000+-track playlist would take minutes."""
    keysets, srcs, buckets = {}, {}, {}
    for src, by_cid in canon.items():
        for cid, norm in by_cid.items():
            if cid.startswith("k:"):
                continue
            if cid not in keysets:
                keysets[cid] = {k.replace("|", " ") for k in spotify_track_keys(norm)}
                keysets[cid] |= {romanized(k) for k in keysets[cid]}
                name_toks = track_key(norm["name"], "").split("|")[0].split()
                buckets.setdefault(name_toks[0] if name_toks else "", []).append(cid)
            srcs.setdefault(cid, set()).add(src)
    n_sources = len(canon)
    pairs = []
    for cids in buckets.values():
        cids.sort()
        for i, a in enumerate(cids):
            for b in cids[i + 1:]:
                if len(srcs[a]) == n_sources and len(srcs[b]) == n_sources:
                    continue
                if any(fuzzy_in(q, keysets[b]) for q in keysets[a]):
                    pairs.append((a, b, sorted(srcs[a]), sorted(srcs[b])))
    return pairs


def _main(argv=None):
    import argparse
    import os

    from dotenv import load_dotenv

    from . import spotify
    from .config import (DEFAULT_CACHE_FILE, DEFAULT_PROVIDERS, DEFAULT_SONG_CACHE_FILE,
                         DEFAULT_SPOTIFY_CACHE_FILE, DEFAULT_STOREFRONT)
    from .runner import load_cache
    from .targets import build_peers

    ap = argparse.ArgumentParser(
        prog="python -m songmirror.engine.dedupe",
        description="Find (and with --execute remove) duplicate playlist copies left by identity splits.")
    ap.add_argument("--execute", action="store_true",
                    help="Remove the duplicate copies (default: report only).")
    ap.add_argument("--playlists", default="",
                    help="Comma-separated names (default: every N-way-managed playlist).")
    ap.add_argument("--variants", action="store_true",
                    help="Also report variant twins — distinct recordings that fuzzy-match as one song.")
    args = ap.parse_args(argv)

    load_dotenv(os.getenv("SONGMIRROR_ENV_FILE") or ".env", override=True)

    class _Opts:  # just the fields the peer builders read
        providers = os.getenv("PROVIDERS", DEFAULT_PROVIDERS)
        storefront = os.getenv("APPLE_STOREFRONT") or DEFAULT_STOREFRONT
        cache_file = os.getenv("APPLE_CACHE_FILE", DEFAULT_CACHE_FILE)
        spotify_cache_file = os.getenv("SPOTIFY_CACHE_FILE", DEFAULT_SPOTIFY_CACHE_FILE)

    sp = spotify.client(writable=args.execute)
    peers = build_peers(_Opts(), sp)
    songs = archive.connect(os.getenv("SONG_CACHE_FILE", DEFAULT_SONG_CACHE_FILE))
    caches = {p.source: load_cache(p.cache_file) for p in peers}
    dirs = {p.source: p.list_playlists() for p in peers}

    managed = {r[0] for r in songs.execute("SELECT DISTINCT playlist FROM playlist_state")}
    wanted = {n.strip().casefold() for n in args.playlists.split(",") if n.strip()} or managed

    total = 0
    for key in sorted(wanted):
        playlists = {src: d[key] for src, d in dirs.items() if d.get(key)}
        active = [p for p in peers if p.source in playlists]
        if not active:
            continue
        entries, canon = scan(active, playlists, caches, songs, key)
        plan, held = dup_plan(active, entries)
        n = sum(len(v) for v in plan.values())
        total += n
        print(f"\n== {key} ==" + ("" if n else "  (no duplicate copies)"))
        for p in active:
            for idx, cid, raw, norm in plan[p.source]:
                print(f"  {p.source:8} #{idx + 1:<4} {norm['name']} - {norm['artist']}   [{cid}]")
            for idx, cid, raw, norm in held[p.source]:
                print(f"  {p.source:8} #{idx + 1:<4} KEPT (version differs from its twin): "
                      f"{norm['name']} - {norm['artist']}   [{cid}]")
        if n and args.execute:
            result = apply(active, playlists, caches, songs, key, plan)
            for src, (removed, after) in sorted(result.items()):
                print(f"  -> {src}: removed {removed}, {after} tracks now")
        if args.variants:
            lookup = {}
            for by_cid in canon.values():
                for cid, norm in by_cid.items():
                    lookup.setdefault(cid, norm)
            vps = variant_pairs(canon)
            if vps:
                print(f"  -- {len(vps)} variant twin(s), report only --")
                for a, b, sa, sb in vps:
                    la, lb = lookup[a], lookup[b]
                    print(f"     {la['name']} - {la['artist']}   [{','.join(sa)}]")
                    print(f"       ~ {lb['name']} - {lb['artist']}   [{','.join(sb)}]")
    print(f"\n{'removed' if args.execute else 'would remove'} {total} duplicate cop"
          f"{'y' if total == 1 else 'ies'} across {len(wanted)} playlist(s)")


if __name__ == "__main__":
    _main()
