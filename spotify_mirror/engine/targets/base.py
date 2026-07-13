"""The mirror target contract + the shared reconciliation algorithms.

A new service (Tidal, Deezer, ...) is added by subclassing `MirrorTarget` and
implementing ~8 small methods (carry ISRC in `playlist_tracks` if the API has
it). Both engines are provider-agnostic and unchanged by a new target:
`mirror_pair` (one-way, Spotify -> target) and `reconcile` (N-way bidirectional
across all peers, diffing each against a stored canonical snapshot). Diff,
resolve, cross-provider identity, ordering, safety rails, logging, and stats
all live here once.
"""

import time

from .. import archive
from ..logs import (
    fmt_counts, fmt_secs, log_add, log_hold, log_miss, log_note, log_remove,
    log_section, log_summary, log_warn, paint,
)
from ..matching import compute_diff, protect_removals, spotify_track_keys, track_key

# A provider reading fewer than this fraction of the known baseline is treated
# as a broken read: its removals are ignored so one bad fetch can't cascade a
# mass-delete across every provider. ponytail: a blunt ratio, not per-provider
# count history — tighten if legitimate drift ever trips it.
COLLAPSE_FRACTION = 0.4


class TargetAuthError(RuntimeError):
    """Auth expired / rejected. Fatal for the pass — never a partial write."""


class MirrorTarget:
    """Interface a mirror destination implements. See apple.py / ytmusic.py."""

    name = "target"       # human label, e.g. "Apple Music"
    tag = "target"        # short log tag, e.g. "apple"
    source = "target"     # archive source key, e.g. "apple"
    cache_file = None     # this target's own resolution cache path (ids differ per service)

    def list_playlists(self):
        """{casefolded name: playlist} of editable-or-not library playlists."""
        raise NotImplementedError

    def is_editable(self, playlist):
        return True

    def create(self, sp_playlist):
        """Create a same-named playlist (name + description copied)."""
        raise NotImplementedError

    def playlist_tracks(self, playlist):
        """Existing tracks as dicts with name/artist/duration_ms + an id."""
        raise NotImplementedError

    def track_id(self, track):
        """Stable id of an existing target track (for diffing / linking)."""
        raise NotImplementedError

    def playlist_count(self, playlist):
        """Current track count from list metadata (no API call), or None. Used
        to catch target-side edits when deciding a snapshot skip."""
        return None

    def playlist_id(self, playlist):
        """Stable id of a library playlist, for explicit pairing lookups."""
        return playlist.get("id")

    def playlist_name(self, playlist):
        """Display name of a library playlist (for transfers / labels)."""
        return playlist.get("name", "")

    def playlist_description(self, playlist):
        return playlist.get("description", "")

    def prefetch(self, sp_tracks, cache):
        """Optional batch work before resolving (Apple: bulk ISRC lookup)."""

    def native_isrc_map(self, cache):
        """{track_id: ISRC} this provider can supply out-of-band (e.g. from its
        own resolve cache) for reads that omit ISRC. Default: none. Overriding
        it lets a new provider unify on ISRC with no reconciler changes."""
        return {}

    def expected_ids(self, sp_tracks, links, cache):
        """{spotify_id: set(target_ids)} the track is known to correspond to."""
        return {t.get("id"): {links[t["id"]]} for t in sp_tracks if links.get(t.get("id"))}

    def resolve(self, sp_track, cache):
        """(target_id, method) for an unlinked track, or (None, None)."""
        raise NotImplementedError

    def add(self, playlist, target_ids):
        """Append target_ids IN ORDER, one request per id (never batch)."""
        raise NotImplementedError

    def remove(self, playlist, track):
        """Remove one existing target track."""
        raise NotImplementedError


def mirror_pair(target, sp_tracks, sp_playlist, tgt_playlist, cache, songs, *, execute, max_removals, max_adds):
    """Reconcile one Spotify→target playlist pair. Returns a stats dict; `clean`
    is True when everything applied with no guard tripped."""
    tag, name = target.tag, sp_playlist.get("name", "?")
    started = time.monotonic()
    tgt_tracks = target.playlist_tracks(tgt_playlist)
    log_section(name, f"Spotify {len(sp_tracks)} tracks - {target.name} {len(tgt_tracks)} tracks", tag=tag)

    archive.upsert_many(songs, "spotify", sp_tracks)
    archive.upsert_many(songs, target.source, tgt_tracks)

    links = archive.get_links(songs, target.source, [t.get("id") for t in sp_tracks])
    target.prefetch(sp_tracks, cache)
    to_add, to_remove = compute_diff(
        sp_tracks, tgt_tracks, target.expected_ids(sp_tracks, links, cache), target.track_id
    )
    if to_add:
        log_note(f"resolving {len(to_add)} new track(s) on {target.name}...", tag=tag)

    # Resolve additions to target ids, preserving the oldest-first order.
    present = {target.track_id(t) for t in tgt_tracks if target.track_id(t)}
    additions, not_found, new_links, methods = [], [], {}, {}
    for i, track in enumerate(to_add, 1):
        label = f"{track['name']} - {', '.join(track['artists'])}"
        tid = links.get(track.get("id"))
        method = "link" if tid else None
        if not tid:
            try:
                tid, method = target.resolve(track, cache)
            except TargetAuthError:
                raise
            except Exception as e:
                log_warn(f"resolve failed: {label}: {e!r}", tag=tag)
                tid, method = None, None
        if len(to_add) > 25 and i % 25 == 0:
            log_note(f"  ...resolved {i}/{len(to_add)}", tag=tag)
        if not tid:
            not_found.append(track)
            continue
        if track.get("id"):
            new_links[track["id"]] = tid
        if tid not in present:
            method = method or "search"
            additions.append((tid, label, method))
            present.add(tid)
            methods[method] = methods.get(method, 0) + 1
    archive.set_links(songs, target.source, new_links)

    guard = False
    deferred = 0
    if len(additions) > max_adds:
        deferred = len(additions) - max_adds
        log_warn(f"{len(additions)} additions exceed --max-adds={max_adds}; deferring {deferred} to next pass", tag=tag)
        additions, guard = additions[:max_adds], True

    removals, held = protect_removals(to_remove, not_found)
    if not sp_tracks and tgt_tracks:
        log_warn(f"Spotify returned 0 tracks but {target.name} has {len(tgt_tracks)}; skipping all removals this pass", tag=tag)
        removals, guard = [], True
    elif len(removals) > max_removals:
        log_warn(f"{len(removals)} removals exceed --max-removals={max_removals}; skipping removals this pass", tag=tag)
        removals, guard = [], True

    for _, label, method in additions:
        log_add(f"{label}  {paint('(' + method + ')', 'grey')}", dry=not execute, tag=tag)
    for track in removals:
        log_remove(f"{track['name']} - {track['artist']}", dry=not execute, tag=tag)
    for track in held:
        log_hold(f"kept (no {target.name} match for its Spotify twin): {track['name']} - {track['artist']}", tag=tag)
    for track in not_found:
        log_miss(f"not on {target.name}: {track['name']} - {', '.join(track['artists'])}", tag=tag)

    if execute:
        if additions:
            target.add(tgt_playlist, [tid for tid, _, _ in additions])
        for track in removals:
            target.remove(tgt_playlist, track)

    via = ", ".join(f"{n} {m}" for m, n in sorted(methods.items(), key=lambda kv: -kv[1]))
    counts = fmt_counts(len(additions), len(removals), len(not_found), len(held), deferred)
    log_summary(
        f"{name}: {counts}  {paint('in ' + fmt_secs(time.monotonic() - started), 'grey')}"
        + (paint(f"  via {via}", "grey") if via else ""),
        tag=tag,
    )
    return {
        "clean": execute and not guard, "added": len(additions), "removed": len(removals),
        "missing": len(not_found), "held": len(held), "deferred": deferred,
        "target_count": len(tgt_tracks) + len(additions) - len(removals),
    }


# --------------------------------------------------------------------------- #
# N-way bidirectional reconcile (SYNC_MODE=nway). Diffs every provider against
# a stored canonical snapshot so a change on ANY provider propagates to all.
# --------------------------------------------------------------------------- #

def _normalize(track, source):
    """Common cross-provider shape, keeping the raw provider dict for removal
    (which needs the relationship_id / playlistItem id / uri)."""
    artists = track.get("artists") or ([track["artist"]] if track.get("artist") else [""])
    return {
        "name": track.get("name", ""),
        "artists": artists,
        "artist": track.get("artist") or ", ".join(a for a in artists if a),
        "duration_ms": track.get("duration_ms"),
        "isrc": track.get("isrc"),
        "added_at": track.get("added_at") or "",
        "_raw": track,
        "_source": source,
    }


def _canonicalize(target, tracks, songs, cache, key2isrc):
    """{canonical_id: normalized track} for one provider's current tracks.

    Canonical precedence: ISRC (direct / provider-native map / same-playlist
    Spotify track_key) -> ISRC via the reverse link to Spotify -> the Spotify id
    -> track_key. Getting the same song onto ONE canonical id across providers
    is the crux, so ISRC is pulled from wherever each provider exposes it:
    Spotify carries it inline; Apple's ISRC resolve cache maps catalog_id ->
    ISRC; and key2isrc (built from this playlist's Spotify tracks) rescues any
    remaining track whose fuzzy key already exists on Spotify — without it, an
    ISRC-less YT copy of a Spotify song splits into a duplicate."""
    rev = ({} if target.source == "spotify"
           else archive.get_reverse_links(songs, target.source, [target.track_id(t) for t in tracks]))
    sp_isrc = archive.get_isrcs(songs, "spotify", list(rev.values())) if rev else {}
    id2isrc = target.native_isrc_map(cache)  # provider-supplied track_id -> ISRC (Apple, future providers)
    out = {}
    for t in tracks:
        norm = _normalize(t, target.source)
        isrc = (norm["isrc"] or id2isrc.get(target.track_id(t))
                or key2isrc.get(track_key(norm["name"], norm["artist"])))
        if isrc:
            cid = f"i:{isrc}"
        else:
            sp_id = rev.get(target.track_id(t))
            if sp_id:
                cid = f"i:{sp_isrc[sp_id]}" if sp_id in sp_isrc else f"s:{sp_id}"
            else:
                cid = f"k:{track_key(norm['name'], norm['artist'])}"
        out.setdefault(cid, norm)  # first occurrence wins (dedupe within a provider)
    return out


def _merge(prev, cur, collapsed):
    """Pure delta merge over PER-PROVIDER state. prev, cur: {source:
    set(canonical_id)} — each provider's membership after the last clean pass
    and now. collapsed: sources whose read is untrusted (skipped this pass).
    Returns (desired, {source: (add_ids, remove_ids)}).

    A canonical is REMOVED only when it leaves a provider that actually had it
    (prev[src] - cur[src]) — so a track that merely can't be matched on a
    service (never in that service's prev) is never mistaken for a deletion.
    add-wins on conflict; desired is the union of prior memberships plus new
    additions minus real removals."""
    adds, removes = set(), set()
    for src, ids in cur.items():
        if src in collapsed:
            continue  # untrusted read contributes neither adds nor removes
        adds |= ids - prev.get(src, set())
        removes |= prev.get(src, set()) - ids
    removes -= adds
    union_prev = set().union(*prev.values()) if prev else set()
    desired = (union_prev | adds) - removes
    plan = {src: (desired - ids, ids - desired) for src, ids in cur.items()}
    return desired, plan


def reconcile(peers, name, playlists, caches, songs, *, execute, max_removals, max_adds, link_key=None):
    """Reconcile one logical playlist across N provider peers, bidirectionally.

    playlists: {source: playlist dict}; caches: {source: resolution cache}.
    `link_key`, when given (explicit pairing), addresses the canonical snapshot
    state so differently-named paired playlists share one logical identity;
    otherwise the casefolded display name is used (implicit same-name pairing).
    Returns a stats dict; `clean` is True when every side applied with no guard
    tripped (only then is the canonical snapshot advanced)."""
    key = link_key or name.casefold()
    started = time.monotonic()
    prev = {p.source: archive.get_playlist_state(songs, key, p.source) for p in peers}

    canon = {}         # source -> {canonical_id: normalized track}
    present = {}       # source -> set of ALL current target ids (not canonical-deduped)
    present_keys = {}  # source -> set of track_keys already on the provider (dupe guard)
    key2isrc = {}      # track_key -> ISRC, seeded by any ISRC-bearing provider (peers are ISRC-rich first)
    for p in peers:
        raw = p.playlist_tracks(playlists[p.source])
        archive.upsert_many(songs, p.source, raw)
        present[p.source] = {p.track_id(t) for t in raw if p.track_id(t)}
        canon[p.source] = _canonicalize(p, raw, songs, caches[p.source], key2isrc)
        present_keys[p.source] = set().union(*(spotify_track_keys(n) for n in canon[p.source].values())) \
            if canon[p.source] else set()
        for cid, norm in canon[p.source].items():
            if cid.startswith("i:"):  # any provider that resolved an ISRC anchors the rest
                key2isrc.setdefault(track_key(norm["name"], norm["artist"]), cid[2:])
    cur = {src: set(m) for src, m in canon.items()}

    repr_ = {}  # canonical_id -> representative track (peers are ordered spotify-first for ISRC-rich reprs)
    for p in peers:
        for cid, norm in canon[p.source].items():
            repr_.setdefault(cid, norm)

    collapsed = set()
    for p in peers:
        base = prev[p.source]
        if base and (not cur[p.source] or len(cur[p.source]) < COLLAPSE_FRACTION * len(base)):
            collapsed.add(p.source)
            log_warn(f"{name}: {p.name} read {len(cur[p.source])} vs baseline {len(base)} — "
                     "ignoring its removals this pass", tag=p.tag)

    desired, plan = _merge(prev, cur, collapsed)
    log_section(name, " / ".join(f"{p.name} {len(cur[p.source])}" for p in peers), tag="sync")

    stats = {"clean": execute and not collapsed, "added": 0, "removed": 0, "missing": 0, "held": 0, "deferred": 0}
    new_links = {p.source: {} for p in peers}
    new_state = {}   # source -> canonical membership to persist (only on a clean pass)
    for p in peers:
        if p.source in collapsed:
            continue  # untrusted read: don't write to it this pass (guards adds too, not just removes)
        add_ids, remove_ids = plan[p.source]
        cache = caches[p.source]
        seen = set(present[p.source])  # every id already on the provider (+ ids queued this pass)

        # ADD: resolve each missing canonical id to this provider's track id.
        add_norms = [repr_[cid] for cid in add_ids]
        try:
            p.prefetch(add_norms, cache)
        except Exception as e:
            log_warn(f"{p.name} prefetch failed: {e!r}", tag=p.tag)
        additions, not_found = [], []
        for norm in sorted(add_norms, key=lambda n: n["added_at"]):
            if spotify_track_keys(norm) & present_keys[p.source]:
                continue  # song already on the provider under a different id — no dupe, and no wasted search
            try:
                tid, method = p.resolve(norm, cache)
            except TargetAuthError:
                raise
            except Exception as e:
                log_warn(f"resolve failed on {p.name}: {norm['name']}: {e!r}", tag=p.tag)
                tid, method = None, None
            if not tid:
                not_found.append(norm)
                continue
            if tid in seen:
                continue  # resolved to a track already present (belt-and-suspenders with the key guard)
            seen.add(tid)
            present_keys[p.source] |= spotify_track_keys(norm)  # so a second add of the same song this pass is caught
            additions.append((tid, method or "search", norm))
            if norm["_source"] == "spotify" and norm["_raw"].get("id"):
                new_links[p.source][norm["_raw"]["id"]] = tid

        deferred = 0
        if len(additions) > max_adds:
            deferred = len(additions) - max_adds
            log_warn(f"{p.name}/{name}: {len(additions)} additions exceed --max-adds={max_adds}; "
                     f"deferring {deferred}", tag=p.tag)
            additions, stats["clean"] = additions[:max_adds], False

        # REMOVE: canonical ids that left the set, guarded by protect_removals + cap.
        remove_pairs = [(cid, canon[p.source][cid]) for cid in remove_ids]
        safe, held = protect_removals([n for _, n in remove_pairs], not_found)
        safe_ids = {id(n) for n in safe}
        removed_cids = {cid for cid, n in remove_pairs if id(n) in safe_ids}
        if len(safe) > max_removals:
            log_warn(f"{p.name}/{name}: {len(safe)} removals exceed --max-removals={max_removals}; "
                     "skipping removals this pass", tag=p.tag)
            safe, removed_cids, stats["clean"] = [], set(), False

        for tid, method, norm in additions:
            log_add(f"{p.name}: {norm['name']} - {norm['artist']}  {paint('(' + method + ')', 'grey')}",
                    dry=not execute, tag=p.tag)
        for norm in safe:
            log_remove(f"{p.name}: {norm['name']} - {norm['artist']}", dry=not execute, tag=p.tag)
        for norm in held:
            log_hold(f"{p.name}: kept (no re-add match): {norm['name']} - {norm['artist']}", tag=p.tag)
        for norm in not_found:
            log_miss(f"not on {p.name}: {norm['name']} - {', '.join(norm['artists'])}", tag=p.tag)

        if execute:
            if additions:
                p.add(playlists[p.source], [tid for tid, _, _ in additions])
            for norm in safe:
                p.remove(playlists[p.source], norm["_raw"])

        # This provider's membership after the pass = what it has now, minus what
        # we removed. Added tracks re-materialize (under their own canonical) on
        # the next read — recording only what's actually present avoids a stale
        # snapshot ever triggering a phantom removal.
        new_state[p.source] = cur[p.source] - removed_cids

        stats["added"] += len(additions)
        stats["removed"] += len(safe)
        stats["missing"] += len(not_found)
        stats["held"] += len(held)
        stats["deferred"] += deferred

    if execute:
        for p in peers:
            archive.set_links(songs, p.source, new_links[p.source])
        if stats["clean"]:
            for src, ids in new_state.items():
                archive.set_playlist_state(songs, key, src, ids)

    counts = fmt_counts(stats["added"], stats["removed"], stats["missing"], stats["held"], stats["deferred"])
    log_summary(f"{name}: {counts}  {paint('in ' + fmt_secs(time.monotonic() - started), 'grey')}", tag="sync")
    return stats
