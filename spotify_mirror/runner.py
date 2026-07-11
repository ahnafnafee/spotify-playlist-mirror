"""Orchestration: build targets, run each in its own thread against the
selected playlists, then the optional local download mirror.

Targets run concurrently (separate hosts, separate rate limits) but each stays
internally sequential to preserve append order and avoid robotic bursts.
"""

import json
import os
import threading
import time

from dotenv import load_dotenv

from . import archive, spotify
from .logs import fmt_counts, fmt_secs, log, log_note, log_section, log_summary, log_warn, paint
from .targets import TargetAuthError, build_targets, mirror_pair


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def load_cache(cache_file):
    try:
        with open(cache_file) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {"isrc": data.get("isrc", {}), "search": data.get("search", {}), "dirty": False}


def save_cache(cache_file, cache):
    if not cache.pop("dirty", False):
        return
    with open(cache_file, "w") as f:
        json.dump({"isrc": cache["isrc"], "search": cache["search"]}, f, indent=1)


def run_target(target, selected, get_sp_tracks, songs, opts):
    """Mirror every selected playlist to one target. Returns an aggregate dict.
    Raises TargetAuthError to abort the whole target (fail closed)."""
    agg = {"name": target.name, "pairs": 0, "added": 0, "removed": 0,
           "missing": 0, "held": 0, "skipped": 0, "created": 0}
    cache = load_cache(target.cache_file)
    try:
        tgt_by_name = target.list_playlists()
        for sp_playlist in selected:
            key = sp_playlist["name"].strip().casefold()
            tgt = tgt_by_name.get(key)
            if not tgt:
                if not opts.execute:
                    log_note(f"{sp_playlist['name']}: no {target.name} playlist yet - would create on --execute", tag=target.tag)
                    continue
                try:
                    tgt = target.create(sp_playlist)
                    agg["created"] += 1
                    log_note(f"created {target.name} playlist '{sp_playlist['name']}' (name + description copied)", tag=target.tag)
                except Exception as e:
                    log_warn(f"create '{sp_playlist['name']}' failed: {e!r}", tag=target.tag)
                    continue

            snapshot = sp_playlist.get("snapshot_id")
            if opts.execute and snapshot:
                state = archive.get_state(songs, key, target.source)
                current = target.playlist_count(tgt)
                if state and state[0] == snapshot and (state[1] is None or current is None or current == state[1]):
                    log_note(f"{sp_playlist['name']}: unchanged since last sync - skipped", tag=target.tag)
                    agg["skipped"] += 1
                    continue

            if not target.is_editable(tgt):
                log_warn(f"'{sp_playlist['name']}': {target.name} playlist not editable - skipped", tag=target.tag)
                continue

            try:
                res = mirror_pair(
                    target, get_sp_tracks(sp_playlist["id"]), sp_playlist, tgt, cache, songs,
                    execute=opts.execute, max_removals=opts.max_removals, max_adds=opts.max_adds,
                )
                agg["pairs"] += 1
                for k in ("added", "removed", "missing", "held"):
                    agg[k] += res[k]
                if res["clean"] and snapshot:
                    archive.set_state(songs, key, target.source, snapshot, res["target_count"])
            except TargetAuthError:
                raise
            except Exception as e:
                log_warn(f"'{sp_playlist.get('name', '?')}' failed, continuing: {e!r}", tag=target.tag)
    finally:
        save_cache(target.cache_file, cache)
    return agg


def run_pass(opts):
    load_dotenv(override=True)  # pick up re-captured tokens without a restart
    sp = spotify.client()
    sp_by_name = spotify.playlists_by_name(sp)

    wanted = {n.strip().casefold() for n in opts.playlists.split(",") if n.strip()} if opts.playlists else None
    selected = [sp_by_name[n] for n in sorted(sp_by_name) if wanted is None or n in wanted]

    mode = paint("EXECUTE", "green", "bold") if opts.execute else paint("DRY RUN", "yellow", "bold")
    log(paint("═══ Spotify playlist mirror ═══", "bold", "cyan"))
    log(f"  mode: {mode}")
    log(f"  playlists: {paint(str(len(selected)), 'bold')} selected"
        + (paint(f" ({', '.join(p['name'] for p in selected)})", "grey") if selected else ""))
    if wanted:
        missing = wanted - {p["name"].strip().casefold() for p in selected}
        if missing:
            log_warn(f"not found on Spotify: {', '.join(sorted(missing))}", indent="  ")

    if opts.refresh_local:
        if not opts.download_dir:
            log_warn("--refresh-local needs a download dir (set DOWNLOAD_DIR or --download-dir)", indent="  ")
            return
        from . import downloads

        downloads.refresh(sp, selected, opts.download_dir)
        return

    targets = build_targets(opts)
    if not targets:
        log_warn("no mirror targets configured (set Apple tokens and/or YouTube Music auth)", indent="  ")
        return
    log(f"  targets: {paint(', '.join(t.name for t in targets), 'cyan')}"
        + (paint(f"   local downloads -> {opts.download_dir}", "grey") if opts.download_dir and opts.execute else ""))

    songs = archive.connect(opts.song_cache_file)
    sp_memo, sp_lock = {}, threading.Lock()
    # Disk cache of playlist tracks keyed by Spotify's snapshot_id: while a
    # playlist is unchanged its 7-page fetch is served from disk, so passes
    # don't re-hammer Spotify. snapshot_id changes exactly when the playlist
    # does, so there's no staleness (unlike a time-based TTL).
    sp_snap = {p["id"]: p.get("snapshot_id") for p in selected}
    tracks_cache_file = os.getenv("SPOTIFY_TRACKS_CACHE", "spotify_tracks_cache.json")
    tracks_cache = _load_json(tracks_cache_file)
    tracks_state = {"dirty": False}

    def get_sp_tracks(playlist_id):
        # Lock guards the memo/cache AND serialises the shared spotipy client.
        with sp_lock:
            if playlist_id in sp_memo:
                return sp_memo[playlist_id]
            snap = sp_snap.get(playlist_id)
            entry = tracks_cache.get(playlist_id)
            if entry and snap and entry.get("snapshot") == snap:
                sp_memo[playlist_id] = entry["tracks"]  # unchanged since last pass
                return entry["tracks"]
            tracks = spotify.playlist_tracks(sp, playlist_id)
            sp_memo[playlist_id] = tracks
            if snap:
                tracks_cache[playlist_id] = {"snapshot": snap, "tracks": tracks}
                tracks_state["dirty"] = True
            return tracks

    results, errors = {}, []

    def worker(target):
        try:
            results[target.tag] = run_target(target, selected, get_sp_tracks, songs, opts)
        except BaseException as e:  # surface after siblings finish
            errors.append((target, e))

    started = time.monotonic()
    # daemon so a Ctrl+C on the main thread can exit the process even while a
    # worker is mid-request; join in short slices so the interrupt is prompt.
    threads = [threading.Thread(target=worker, args=(t,), name=f"{t.tag}-mirror", daemon=True) for t in targets]
    for t in threads:
        t.start()
    try:
        for t in threads:
            while t.is_alive():
                t.join(0.5)
    finally:
        songs.close()
        if tracks_state["dirty"]:
            _save_json(tracks_cache_file, tracks_cache)

    log_section("Pass complete", fmt_secs(time.monotonic() - started))
    for target in targets:
        agg = results.get(target.tag)
        if not agg:
            continue
        notes = []
        if agg["created"]:
            notes.append(f"{agg['created']} created")
        if agg["skipped"]:
            notes.append(f"{agg['skipped']} unchanged")
        tail = f"  across {agg['pairs']} playlist(s)" + (f" ({', '.join(notes)})" if notes else "")
        log_summary(f"{target.name:<14} {fmt_counts(agg['added'], agg['removed'], agg['missing'], agg['held'])}"
                    + paint(tail, "grey"), indent="  ")

    for target, err in errors:
        if isinstance(err, TargetAuthError):
            raise err  # fatal; main() decides exit vs. loop-continue

    if opts.download_dir and opts.execute:
        try:
            from . import downloads

            downloads.run(sp, selected, opts.download_dir, opts.song_cache_file)
        except Exception as e:
            log_warn(f"local download mirror failed (playlist sync unaffected): {e!r}", tag="local")

    # Push real playlist covers to Jellyfin (opt-in; no-op without JELLYFIN_*).
    if opts.execute:
        from . import jellyfin

        jellyfin.push_covers(selected)
