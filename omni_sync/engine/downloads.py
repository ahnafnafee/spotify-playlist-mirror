"""Local audio mirror via spotDL, in a Jellyfin-ready layout.

One folder per playlist, `AlbumArtist/Album/Artists - Title.<ext>` inside, plus
a `<Playlist>.m3u8` Jellyfin imports as the playlist and the Spotify cover art
saved as `cover.jpg`/`folder.jpg`. spotDL's `sync` is incremental and
resumable: completed files are skipped, tracks removed from the playlist are
deleted, and an interrupted run just continues on the next pass (only the file
being downloaded when it stopped is re-fetched). Each pass restamps every
file's mtime to its Spotify added-at date so Date-Modified sort = date-added.
"""

import importlib.util
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from . import spotify
from .logs import fmt_secs, log_download, log_miss, log_note, log_section, log_summary, log_warn
from .matching import normalize_text as _norm

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav"}
DEFAULT_TIMEOUT_S = 3600  # ponytail: blunt per-playlist cap; a killed run resumes next pass


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def sanitize_folder(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip().rstrip(" .")
    return cleaned or "playlist"


def ffmpeg_available():
    if shutil.which("ffmpeg"):
        return True
    return (Path.home() / ".spotdl" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).is_file()


def read_tracks(sp, playlist_id):
    """Ordered per-track info from the live playlist: added-at, ISRC, match
    keys, and display name/artist/duration."""
    out = []
    page = spotify._retry(lambda: sp.playlist_items(playlist_id, additional_types=("track",), limit=100), "playlist_items")
    while page:
        for item in page.get("items", []):
            track = spotify.playlist_item_track(item)
            added = item.get("added_at")
            if not added or not track:
                continue
            try:
                when = datetime.fromisoformat(added.replace("Z", "+00:00"))
            except ValueError:
                continue
            name = track.get("name", "")
            artists = [a.get("name", "") for a in track.get("artists", []) if a.get("name")]
            isrc = (track.get("external_ids") or {}).get("isrc")
            title = _norm(name)
            keys = set()
            if title:
                for artist in {_norm(artists[0] if artists else ""), _norm(" ".join(artists))}:
                    if artist:
                        keys.add(f"{artist}|{title}")
            album = track.get("album") or {}
            images = album.get("images") or []
            out.append({"id": track.get("id"), "when": when,
                        "isrc": isrc.strip().upper() if isrc else None, "keys": keys,
                        "name": name, "artist": ", ".join(artists), "album": album.get("name"),
                        "image": images[0].get("url") if images else None,  # largest first
                        "duration_ms": track.get("duration_ms")})
        prev = page
        page = spotify._retry(lambda: sp.next(prev), "tracks page") if page.get("next") else None
    return out


def added_at_indexes(tracks):
    by_isrc, by_key = {}, {}
    for t in tracks:
        if t["isrc"]:
            by_isrc[t["isrc"]] = t["when"]
        for key in t["keys"]:
            by_key[key] = t["when"]
    return by_isrc, by_key


def match_added_at(isrcs, title, raw_artists, by_isrc, by_key):
    for isrc in isrcs:
        when = by_isrc.get(str(isrc).strip().upper())
        if when:
            return when
    title = _norm(title)
    if not title:
        return None
    for raw in raw_artists:
        for artist in (_norm(raw), _norm(re.split(r"[,;/]", raw)[0])):
            when = by_key.get(f"{artist}|{title}")
            if when:
                return when
    return None


def _track_lookups(tracks):
    """Track objects keyed by ISRC, artist|title, and filename-stem — for
    backfilling tags onto files, including ones spotDL tagged poorly (matched by
    its '{artists} - {title}' filename when the file itself has no tags)."""
    by_isrc, by_key, by_stem = {}, {}, {}
    for t in tracks:
        if t.get("isrc"):
            by_isrc.setdefault(t["isrc"], t)
        for k in t["keys"]:
            by_key.setdefault(k, t)
        joined, title = t["artist"], t["name"]
        primary = joined.split(",")[0].strip()
        for combo in {_norm(f"{primary} - {title}"), _norm(f"{joined} - {title}")}:
            if combo:
                by_stem.setdefault(combo, t)
    return by_isrc, by_key, by_stem


def _match_track(isrcs, title, artists, stem, by_isrc, by_key, by_stem):
    for i in isrcs:
        t = by_isrc.get(str(i).strip().upper())
        if t:
            return t
    nt = _norm(title)
    if nt:
        for raw in artists:
            for a in (_norm(raw), _norm(re.split(r"[,;/]", raw)[0])):
                t = by_key.get(f"{a}|{nt}")
                if t:
                    return t
    return by_stem.get(_norm(stem))  # untagged file -> match by spotDL's filename


def _fill_missing(audio, track):
    """Set Jellyfin-relevant tags that are MISSING; never overwrite what spotDL
    already wrote. Returns True if anything was added."""
    primary = (track.get("artist") or "").split(",")[0].strip()
    wanted = {
        "title": track.get("name"),
        "artist": track.get("artist"),
        "album": track.get("album"),
        "albumartist": primary or None,
        "isrc": track.get("isrc"),
    }
    changed = False
    for key, value in wanted.items():
        if value and not audio.get(key):
            try:
                audio[key] = [value]
                changed = True
            except (KeyError, ValueError, TypeError):
                pass  # tag unsupported for this file format
    return changed


def _fetch_image(url, cache):
    if url not in cache:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            cache[url] = r.content
        except Exception:
            cache[url] = None
    return cache[url]


def _embed_cover(path, data):
    """Embed cover art if the file has none (mp3/flac/m4a). Returns True if
    added. Never overwrites art spotDL already embedded; other formats rely on
    the album-folder cover.jpg instead."""
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import APIC, ID3, ID3NoHeaderError
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            if tags.getall("APIC"):
                return False
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data))
            tags.save(path)
            return True
        if ext == ".flac":
            from mutagen.flac import FLAC, Picture
            f = FLAC(path)
            if f.pictures:
                return False
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", data
            f.add_picture(pic)
            f.save()
            return True
        if ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            f = MP4(path)
            if f.get("covr"):
                return False
            f["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
            f.save()
            return True
    except Exception:
        return False
    return False


def build_m3u(tracks, file_by_isrc, file_by_key, all_rels, newest_first=True):
    """m3u8 lines ordered by Spotify date-added (newest first by default), each
    resolved to a downloaded file via ISRC then artist|title. Downloaded files
    that match no current track are kept at the end so nothing is dropped."""
    lines, used = ["#EXTM3U"], set()
    for t in sorted(tracks, key=lambda t: t["when"], reverse=newest_first):
        rel = (t["isrc"] and file_by_isrc.get(t["isrc"])) \
            or next((file_by_key[k] for k in t["keys"] if k in file_by_key), None)
        if not rel or rel in used:
            continue
        used.add(rel)
        lines.append(f"#EXTINF:{int((t['duration_ms'] or 0) / 1000)},{t['artist']} - {t['name']}")
        lines.append(rel)
    for rel in all_rels:
        if rel not in used:
            used.add(rel)
            lines.append(rel)
    return lines


def finalize_folder(folder, tracks, newest_first=True):
    """One tag scan of the folder that does everything per audio file: stamp its
    mtime to the Spotify added-at date, backfill any missing Jellyfin tags
    (title/artist/album/albumartist/isrc) + cover art from Spotify, and index it
    for the date-ordered `<folder>.m3u8`. Returns
    (stamped, unmatched, tagged, id_to_file, missing_ids) — the last two map
    each track to its downloaded file (for removals) and list tracks with none
    (unavailable), so the caller doesn't need a second scan."""
    import mutagen  # spotDL dependency

    by_isrc, by_key = added_at_indexes(tracks)
    t_by_isrc, t_by_key, t_by_stem = _track_lookups(tracks)
    backfill = os.getenv("LOCAL_MIRROR_TAG_BACKFILL", "1") != "0"
    file_by_isrc, file_by_key, file_by_stem, all_rels, img_cache = {}, {}, {}, [], {}
    stamped = unmatched = tagged = 0
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            continue
        rel = path.relative_to(folder).as_posix()
        all_rels.append(rel)
        try:
            audio = mutagen.File(path, easy=True)
        except Exception:
            audio = None
        isrcs = (audio.get("isrc") if audio else []) or []
        title = (audio.get("title") if audio else [""])[0] if audio else ""
        artists = (audio.get("artist") if audio else []) or []
        for i in isrcs:
            file_by_isrc.setdefault(str(i).strip().upper(), rel)
        norm_title = _norm(title)
        if norm_title:
            for raw in artists:
                for artist in (_norm(raw), _norm(re.split(r"[,;/]", raw)[0])):
                    if artist:
                        file_by_key.setdefault(f"{artist}|{norm_title}", rel)
        file_by_stem.setdefault(_norm(path.stem), rel)  # untagged files match by filename
        when = match_added_at(isrcs, title, artists, by_isrc, by_key)
        if when:
            os.utime(path, (when.timestamp(), when.timestamp()))
            stamped += 1
        else:
            unmatched += 1
        if backfill and audio is not None:
            track = _match_track(isrcs, title, artists, path.stem, t_by_isrc, t_by_key, t_by_stem)
            if track:
                touched = _fill_missing(audio, track)
                if touched:
                    try:
                        audio.save()
                    except Exception:
                        touched = False
                # Album image for Jellyfin: a cover.jpg in the album folder,
                # plus embedded art on any file that lacks it. Only spend a
                # download when the folder art is missing or we just fixed tags.
                cover = path.parent / "cover.jpg"
                if track.get("image") and (not cover.exists() or touched):
                    data = _fetch_image(track["image"], img_cache)
                    if data:
                        if not cover.exists():
                            try:
                                cover.write_bytes(data)
                                touched = True
                            except Exception:
                                pass
                        if _embed_cover(path, data):
                            touched = True
                if touched:
                    tagged += 1

    lines = build_m3u(tracks, file_by_isrc, file_by_key, all_rels, newest_first)
    (folder / f"{folder.name}.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")

    id_to_file, missing_ids = {}, []
    for t in tracks:
        rel = (t.get("isrc") and file_by_isrc.get(t["isrc"])) \
            or next((file_by_key[k] for k in t["keys"] if k in file_by_key), None)
        if not rel:
            primary = t["artist"].split(",")[0].strip()
            rel = next((file_by_stem[s] for s in (_norm(f"{primary} - {t['name']}"), _norm(f"{t['artist']} - {t['name']}"))
                        if s in file_by_stem), None)
        if rel and t.get("id"):
            id_to_file[t["id"]] = rel
        elif not rel and t.get("id"):
            missing_ids.append(t["id"])
    return stamped, unmatched, tagged, id_to_file, missing_ids


def save_cover(playlist, folder):
    """Save the highest-resolution Spotify playlist cover as cover.jpg +
    folder.jpg (Jellyfin folder image). Re-downloads only when the URL changes."""
    images = playlist.get("images") or []
    url = images[0].get("url") if images else None  # Spotify lists largest first
    if not url:
        return
    marker = folder / ".cover_url"
    if (folder / "cover.jpg").exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == url:
        return
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        for fname in ("cover.jpg", "folder.jpg"):
            (folder / fname).write_bytes(r.content)
        marker.write_text(url, encoding="utf-8")
        log_download(f"cover art saved ({len(r.content) // 1024} KB)", tag="local")
    except Exception as e:
        log_warn(f"cover art failed: {e!r}", tag="local")


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _spotdl_cmd():
    """Base command to invoke spotDL, kept out of this app's own interpreter so
    spotDL's tight pins (old FastAPI/uvicorn) never constrain the web stack.
    Precedence: SPOTDL_CMD override -> a `spotdl` on PATH (recommended:
    `uv tool install spotdl` / `pipx install spotdl`) -> spotDL in this venv.
    Returns None when spotDL isn't reachable any of those ways."""
    override = os.getenv("SPOTDL_CMD")
    if override:
        return shlex.split(override)
    if shutil.which("spotdl"):
        return ["spotdl"]
    if importlib.util.find_spec("spotdl") is not None:
        return [sys.executable, "-m", "spotdl"]
    return None


def build_download_cmd(queries):
    """`spotdl download <queries>` — queries is a playlist URL (full download)
    or specific track URLs (incremental). No `sync`, so no whole-playlist
    re-processing when only a few tracks are new; removals are handled here
    instead, and the m3u is written by finalize_folder in date-added order."""
    cmd = [*(_spotdl_cmd() or [sys.executable, "-m", "spotdl"]), "download", *queries]
    cmd += [
        "--output", "{album-artist}/{album}/{artists} - {title}.{output-ext}",
        "--overwrite", "skip",  # never re-download a file that already exists (resume-friendly)
        "--simple-tui",
    ]
    # Fall back from YouTube Music to plain YouTube: OSTs / instrumentals /
    # indie tracks that aren't catalog "songs" exist there as videos.
    cmd += ["--audio", *os.getenv("LOCAL_MIRROR_AUDIO_PROVIDERS", "youtube-music youtube").split()]
    client_id, client_secret = os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")
    if client_id and client_secret:
        cmd += ["--client-id", client_id, "--client-secret", client_secret]
    audio_format = os.getenv("LOCAL_MIRROR_FORMAT")
    if audio_format:
        cmd += ["--format", audio_format]
    bitrate = os.getenv("LOCAL_MIRROR_BITRATE")
    if bitrate:  # e.g. 320k, or "disable" to copy the source without re-encoding
        cmd += ["--bitrate", bitrate]
    cookie_file = os.getenv("LOCAL_MIRROR_COOKIE_FILE")
    if cookie_file:  # a YT Music Premium cookie file unlocks 256 kbps AAC
        cmd += ["--cookie-file", cookie_file]
    return cmd


def _kill_tree(proc):
    """Kill spotDL and its yt-dlp children. On Windows proc.kill() alone orphans
    the grandchildren, so use taskkill /T to take down the whole tree."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            proc.kill()
    except Exception:
        pass


def _stream_spotdl(cmd, folder, timeout_s):
    """Run spotDL, streaming meaningful lines live. Returns (downloaded, skipped,
    return_code). A watchdog kills a hung run after timeout_s; because the read
    loop ends when the pipe closes, completed downloads are preserved and the
    next pass resumes."""
    verbose = os.getenv("LOCAL_MIRROR_VERBOSE") == "1"
    counts = {"downloaded": 0, "skipped": 0}
    # PYTHONUNBUFFERED so spotDL's lines reach us promptly (a piped child
    # block-buffers otherwise); PYTHONUTF8/IOENCODING so its own logging doesn't
    # crash (cp1252) on non-Latin track names.
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=str(folder), env=env)

    # Read spotDL's output on a side thread into a queue so the MAIN loop polls
    # with a timeout and stays interruptible — a blocking readline on the main
    # thread can't be broken by Ctrl+C on Windows, which is why it "wouldn't stop".
    lines = queue.Queue()

    def reader():
        try:
            for raw in proc.stdout:
                lines.put(raw)
        finally:
            lines.put(None)  # EOF sentinel

    threading.Thread(target=reader, daemon=True).start()

    killer = threading.Timer(timeout_s, lambda: _kill_tree(proc))
    killer.start()

    # Heartbeat: spotDL can go silent for a while (searching, or downloading one
    # big file), so tick every 15s with the running counts — never looks stuck.
    stop = threading.Event()

    def heartbeat():
        start = time.monotonic()
        while not stop.wait(15):
            log_note(f"...still working: {counts['downloaded']} downloaded, {counts['skipped']} skipped"
                     f" ({fmt_secs(time.monotonic() - start)} elapsed)", tag="local")

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        while True:
            try:
                raw = lines.get(timeout=1)  # returns to Python every 1s, so Ctrl+C lands here
            except queue.Empty:
                continue
            if raw is None:
                break
            line = raw.strip()
            if not line:
                continue
            if verbose:
                log_note(line, tag="local")
            if line.startswith("Downloaded"):
                counts["downloaded"] += 1
                title = line.split('"')[1] if '"' in line else line[len("Downloaded"):].strip(' :')
                log_download(f"downloaded: {title}", tag="local")
            elif line.startswith("Skipping"):
                counts["skipped"] += 1
            elif "No results found" in line:
                log_miss(f"no audio source: {line.split(':', 1)[-1].strip()}", tag="local")
            elif line.startswith("--- Logging error") or "charmap_encode" in line or line.startswith("self.handleError"):
                continue  # spotDL child logging noise (defused by the UTF-8 env above)
            elif "rror" in line or "Exception" in line:  # Error / *Error
                log_warn(line[:200], tag="local")
        proc.wait()
    except KeyboardInterrupt:
        _kill_tree(proc)  # stop spotDL + yt-dlp immediately, then propagate
        raise
    finally:
        stop.set()
        killer.cancel()
        _kill_tree(proc)
    return counts["downloaded"], counts["skipped"], proc.returncode


def _diff_ids(current_ids, prev_files, unavailable):
    """(new_ids, removed_ids): tracks to download (not yet downloaded and not
    known-unavailable) and tracks that left the playlist (whose files we delete).
    First run (empty prev) => every current track is new."""
    have = set(prev_files) | set(unavailable)
    current = set(current_ids)
    return [i for i in current_ids if i not in have], [i for i in have if i not in current]


def _delete_removed(folder, removed_ids, prev_files):
    """Delete the local files of tracks that left the playlist, pruning emptied
    album/artist folders. (spotDL `sync` would do this, but we no longer sync.)"""
    deleted = 0
    for rid in removed_ids:
        rel = prev_files.get(rid)
        if not rel:
            continue  # was unavailable / never downloaded
        path = folder / rel
        try:
            if path.exists():
                path.unlink()
                deleted += 1
            for parent in (path.parent, path.parent.parent):
                if parent != folder and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
        except OSError:
            pass
    return deleted


def _download_one(sp, playlist, folder, timeout_s, tracks, new_ids, removed_ids, prev_files):
    """Delete removed tracks, download only the new ones (whole-playlist on the
    first/large sync, otherwise just the new tracks' URLs so spotDL skips its
    whole-playlist re-processing), then finalize. Returns
    (clean, id_to_file, unavailable)."""
    name = playlist.get("name") or playlist["id"]
    folder.mkdir(parents=True, exist_ok=True)
    save_cover(playlist, folder)
    started = time.monotonic()
    newest_first = os.getenv("LOCAL_MIRROR_ORDER", "newest").strip().lower() != "oldest"

    removed = _delete_removed(folder, removed_ids, prev_files)
    downloaded = code = 0
    if new_ids:
        if not prev_files or len(new_ids) > 40:  # first download or a big change
            pl_url = (playlist.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/playlist/{playlist['id']}"
            log_note(f"'{name}': downloading {len(new_ids)} track(s) (full playlist)...", tag="local")
            downloaded, _, code = _stream_spotdl(build_download_cmd([pl_url]), folder, timeout_s)
        else:  # just the new tracks — no whole-playlist re-processing
            log_note(f"'{name}': downloading {len(new_ids)} new track(s)...", tag="local")
            for chunk in _chunks([f"https://open.spotify.com/track/{i}" for i in new_ids], 40):
                d, _, c = _stream_spotdl(build_download_cmd(chunk), folder, timeout_s)
                downloaded += d
                code = c or code
        if code != 0:
            log_warn(f"'{name}': spotdl exited {code} (partial progress kept; resumes next pass)", tag="local")

    stamped, _, tagged, id_to_file, missing = finalize_folder(folder, tracks, newest_first)
    order = "newest-first" if newest_first else "oldest-first"
    parts = [p for p in (f"{downloaded} downloaded" if downloaded else "",
                         f"{removed} removed" if removed else "",
                         f"{tagged} tagged" if tagged else "",
                         f"{len(missing)} unavailable" if missing else "") if p]
    summary = ", ".join(parts) or "no changes"
    log_summary(f"{name}: {summary}, {stamped} date-stamped, m3u {order}"
                f"  (in {fmt_secs(time.monotonic() - started)})", tag="local")
    return (code == 0), id_to_file, set(missing)


def _folder_for(base, playlist, used):
    name = playlist.get("name") or playlist.get("id", "playlist")
    folder_name = sanitize_folder(name)
    if folder_name.casefold() in used:  # same-named playlists must not collide
        folder_name = f"{folder_name} [{str(playlist.get('id', ''))[:8]}]"
    used.add(folder_name.casefold())
    return base / folder_name


def refresh(sp, spotify_playlists, download_dir):
    """Rebuild covers, mtimes and the newest-first m3u from ALREADY-downloaded
    files — no spotDL, no mirrors. For when you just want the playlist files
    regenerated. Never raises out."""
    try:
        if importlib.util.find_spec("mutagen") is None:
            log_note("refresh skipped: mutagen not installed (uv sync --extra download)", tag="local")
            return
        base = Path(download_dir)
        if not base.exists():
            log_note(f"refresh skipped: {download_dir} does not exist", tag="local")
            return
        newest_first = os.getenv("LOCAL_MIRROR_ORDER", "newest").strip().lower() != "oldest"
        log_section("Refresh local playlists", f"{len(spotify_playlists)} playlist(s) -> {download_dir}", tag="local")
        used = set()
        for playlist in spotify_playlists:
            name = playlist.get("name") or playlist.get("id", "playlist")
            folder = _folder_for(base, playlist, used)
            if not folder.exists():
                log_note(f"'{name}': no download folder yet - skipped", tag="local")
                continue
            try:
                save_cover(playlist, folder)
                stamped, _, tagged, _, _ = finalize_folder(folder, read_tracks(sp, playlist["id"]), newest_first)
                order = "newest-first" if newest_first else "oldest-first"
                extra = f", {tagged} tagged" if tagged else ""
                log_summary(f"{name}: m3u {order}, {stamped} date-stamped{extra}", tag="local")
            except Exception as e:
                log_warn(f"'{name}': {e!r}", tag="local")
    except Exception as e:
        log_warn(f"refresh failed: {e!r}", tag="local")


def run(sp, spotify_playlists, download_dir):
    """Never raises out; logs one skip line if spotdl/ffmpeg aren't set up.

    Per-playlist download state (Spotify snapshot_id, track ids, and the set of
    tracks spotDL couldn't source) lives in a JSON file so spotDL is invoked
    only when it's actually needed: an unchanged playlist is skipped outright,
    and a changed one runs spotDL only if a genuinely new/removed track appears
    (not merely because some already-known-unavailable tracks are 'missing')."""
    try:
        if _spotdl_cmd() is None:
            log_note("local mirror skipped: spotdl not found "
                     "(install it: `uv tool install spotdl` or `pipx install spotdl`)", tag="local")
            return
        if not ffmpeg_available():
            log_note("local mirror skipped: ffmpeg not found (install it or run `spotdl --download-ffmpeg`)", tag="local")
            return

        base = Path(download_dir)
        base.mkdir(parents=True, exist_ok=True)
        timeout_s = int(os.getenv("LOCAL_MIRROR_TIMEOUT", DEFAULT_TIMEOUT_S))
        log_section("Local downloads", f"{len(spotify_playlists)} playlist(s) -> {download_dir}", tag="local")

        state_file = os.getenv("DOWNLOAD_STATE_FILE", "download_state.json")
        state = _load_json(state_file)
        dirty = False
        used = set()
        for playlist in spotify_playlists:
            name = playlist.get("name") or playlist.get("id", "playlist")
            pid = playlist.get("id", "")
            folder = _folder_for(base, playlist, used)
            snapshot = playlist.get("snapshot_id")
            prev = state.get(pid, {})
            if snapshot and prev.get("snapshot") == snapshot and folder.exists():
                log_note(f"'{name}': unchanged since last download - skipped", tag="local")
                continue
            try:
                tracks = read_tracks(sp, pid)
                current_ids = [t["id"] for t in tracks if t.get("id")]
                prev_files = prev.get("files", {})
                new_ids, removed_ids = _diff_ids(current_ids, prev_files, prev.get("unavailable", []))
                if not new_ids and not removed_ids and prev:
                    log_note(f"'{name}': no new or removed tracks - refreshing m3u only", tag="local")
                clean, id_to_file, unavailable = _download_one(
                    sp, playlist, folder, timeout_s, tracks, new_ids, removed_ids, prev_files)
                if clean:
                    state[pid] = {"snapshot": snapshot, "files": id_to_file, "unavailable": sorted(unavailable)}
                    dirty = True
            except Exception as e:
                log_warn(f"'{name}': {e!r}", tag="local")
        if dirty:
            _save_json(state_file, state)
    except Exception as e:
        log_warn(f"local mirror failed: {e!r}", tag="local")
