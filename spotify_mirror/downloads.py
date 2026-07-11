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
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from . import archive, spotify
from .logs import fmt_secs, log_download, log_miss, log_note, log_section, log_summary, log_warn
from .matching import normalize_text as _norm

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav"}
DEFAULT_TIMEOUT_S = 3600  # ponytail: blunt per-playlist cap; a killed run resumes next pass


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
            out.append({"when": when, "isrc": isrc.strip().upper() if isrc else None, "keys": keys,
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
    """One tag scan of the folder that does three things per audio file: stamp
    its mtime to the Spotify added-at date, backfill any missing Jellyfin tags
    (title/artist/album/albumartist/isrc) from Spotify, and index it for the
    date-ordered `<folder>.m3u8` (rewritten at the end). Returns
    (stamped, unmatched, tagged)."""
    import mutagen  # spotDL dependency

    by_isrc, by_key = added_at_indexes(tracks)
    t_by_isrc, t_by_key, t_by_stem = _track_lookups(tracks)
    backfill = os.getenv("LOCAL_MIRROR_TAG_BACKFILL", "1") != "0"
    file_by_isrc, file_by_key, all_rels, img_cache = {}, {}, [], {}
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
    return stamped, unmatched, tagged


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


def build_sync_cmd(folder, save_file, playlist_url):
    cmd = [sys.executable, "-m", "spotdl", "sync"]
    cmd.append(str(save_file) if save_file.exists() else playlist_url)
    if not save_file.exists():
        cmd += ["--save-file", str(save_file)]
    # Same --output every run so sync's delete step recomputes old paths. The
    # playlist .m3u8 is written by finalize_folder (in date-added order), not by
    # spotDL, whose order can't be controlled — so no --m3u here.
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


def _sync_one(sp, playlist, folder, timeout_s):
    name = playlist.get("name") or playlist["id"]
    folder.mkdir(parents=True, exist_ok=True)
    save_cover(playlist, folder)

    save_file = folder / ".sync.spotdl"  # spotDL requires the .spotdl extension
    url = (playlist.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/playlist/{playlist['id']}"
    started = time.monotonic()
    log_note(f"'{name}': syncing downloads (spotDL)...", tag="local")
    downloaded, skipped, code = _stream_spotdl(build_sync_cmd(folder, save_file, url), folder, timeout_s)
    if code != 0:
        log_warn(f"'{name}': spotdl exited {code} (partial progress kept; resumes next pass)", tag="local")

    # Newest-added first (top of the list, like Spotify); LOCAL_MIRROR_ORDER=oldest flips it.
    newest_first = os.getenv("LOCAL_MIRROR_ORDER", "newest").strip().lower() != "oldest"
    stamped, _, tagged = finalize_folder(folder, read_tracks(sp, playlist["id"]), newest_first)
    order = "newest-first" if newest_first else "oldest-first"
    extra = f", {tagged} tagged" if tagged else ""
    log_summary(f"{name}: {downloaded} downloaded, {skipped} already had, {stamped} date-stamped{extra}, m3u {order}"
                f"  (in {fmt_secs(time.monotonic() - started)})", tag="local")
    return code == 0  # clean = spotDL finished (not timed out / errored)


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
                stamped, _, tagged = finalize_folder(folder, read_tracks(sp, playlist["id"]), newest_first)
                order = "newest-first" if newest_first else "oldest-first"
                extra = f", {tagged} tagged" if tagged else ""
                log_summary(f"{name}: m3u {order}, {stamped} date-stamped{extra}", tag="local")
            except Exception as e:
                log_warn(f"'{name}': {e!r}", tag="local")
    except Exception as e:
        log_warn(f"refresh failed: {e!r}", tag="local")


def run(sp, spotify_playlists, download_dir, song_cache_file=None):
    """Never raises out; logs one skip line if spotdl/ffmpeg aren't set up.
    A playlist whose Spotify snapshot is unchanged since its last successful
    download is skipped entirely — spotDL's minutes-long pre-processing (it
    re-fetches the playlist and re-matches every track before reporting a
    single skip) is pure waste when nothing changed."""
    try:
        if importlib.util.find_spec("spotdl") is None:
            log_note("local mirror skipped: spotdl not installed (uv sync --extra download)", tag="local")
            return
        if not ffmpeg_available():
            log_note("local mirror skipped: ffmpeg not found (install it or run `spotdl --download-ffmpeg`)", tag="local")
            return

        base = Path(download_dir)
        base.mkdir(parents=True, exist_ok=True)
        timeout_s = int(os.getenv("LOCAL_MIRROR_TIMEOUT", DEFAULT_TIMEOUT_S))
        log_section("Local downloads", f"{len(spotify_playlists)} playlist(s) -> {download_dir}", tag="local")

        songs = archive.connect(song_cache_file) if song_cache_file else None
        used = set()
        try:
            for playlist in spotify_playlists:
                name = playlist.get("name") or playlist.get("id", "playlist")
                folder = _folder_for(base, playlist, used)
                snapshot = playlist.get("snapshot_id")
                key = (playlist.get("name") or "").strip().casefold()
                if songs is not None and snapshot and key:
                    state = archive.get_state(songs, key, "local")
                    if state and state[0] == snapshot and folder.exists():
                        log_note(f"'{name}': unchanged since last download - skipped", tag="local")
                        continue
                try:
                    clean = _sync_one(sp, playlist, folder, timeout_s)
                    if clean and songs is not None and snapshot and key:
                        archive.set_state(songs, key, "local", snapshot, 0)
                except Exception as e:
                    log_warn(f"'{name}': {e!r}", tag="local")
        finally:
            if songs is not None:
                songs.close()
    except Exception as e:
        log_warn(f"local mirror failed: {e!r}", tag="local")
