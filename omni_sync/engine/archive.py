"""Ever-growing local SQLite archive + resolution memory.

Three tables in one file:
- songs:      every track ever seen on any service (never deleted) — a durable
              metadata record with first/last-seen timestamps.
- links:      spotify_id -> target_id for every successful match, so later
              passes match by hard identifier instead of re-searching.
- sync_state: a playlist's Spotify snapshot_id after a clean pass, so an
              unchanged pair can be skipped wholesale.

SQLite over a pickle blob: incremental writes, crash-safe, and inspectable
(`sqlite3 song_cache.db "SELECT name, artist, last_seen FROM songs"`).
"""

import json
import sqlite3
from datetime import datetime, timezone

SCHEMAS = [
    """
CREATE TABLE IF NOT EXISTS songs (
    source      TEXT NOT NULL,
    id          TEXT NOT NULL,
    isrc        TEXT,
    name        TEXT,
    artist      TEXT,
    album       TEXT,
    duration_ms INTEGER,
    meta        TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (source, id)
)
""",
    """
CREATE TABLE IF NOT EXISTS links (
    spotify_id TEXT NOT NULL,
    target     TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    updated    TEXT NOT NULL,
    PRIMARY KEY (spotify_id, target)
)
""",
    """
CREATE TABLE IF NOT EXISTS sync_state (
    pair         TEXT NOT NULL,
    target       TEXT NOT NULL,
    snapshot_id  TEXT,
    target_count INTEGER,
    updated      TEXT NOT NULL,
    PRIMARY KEY (pair, target)
)
""",
    # N-way sync: the canonical membership of a logical playlist ON EACH PROVIDER
    # after the last clean pass. Per-provider (not one shared set) is essential:
    # a track absent from a provider's own prior membership is never a removal
    # there, so a track that simply can't be matched on that service is not
    # mistaken for a user deletion. See targets/base.py.
    """
CREATE TABLE IF NOT EXISTS playlist_state (
    playlist     TEXT NOT NULL,
    source       TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    PRIMARY KEY (playlist, source, canonical_id)
)
""",
    # Ordered per-provider snapshots of each playlist, kept as a short history.
    # A recovery / forensics trail (what did this playlist look like, in order,
    # and when) — the sync logic itself never reads these back.
    """
CREATE TABLE IF NOT EXISTS playlist_order (
    playlist    TEXT NOT NULL,
    source      TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    tracks      TEXT NOT NULL,
    PRIMARY KEY (playlist, source, captured_at)
)
""",
]

UPSERT = """
INSERT INTO songs (source, id, isrc, name, artist, album, duration_ms, meta, first_seen, last_seen)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source, id) DO UPDATE SET
    isrc = excluded.isrc, name = excluded.name, artist = excluded.artist,
    album = excluded.album, duration_ms = excluded.duration_ms,
    meta = excluded.meta, last_seen = excluded.last_seen
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    # check_same_thread=False: the Apple and YT mirrors run on separate threads,
    # each with its own use of a connection; the timeout rides out any lock.
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    # Migrate a pre-per-provider playlist_state (no `source` column). It's
    # regenerable snapshot state, so drop it and let the schema recreate it.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(playlist_state)").fetchall()]
    if cols and "source" not in cols:
        conn.execute("DROP TABLE playlist_state")
    for schema in SCHEMAS:
        conn.execute(schema)
    conn.commit()
    return conn


def upsert_many(conn, source, tracks):
    """Archive the sync's own snapshot dicts (any service shape). first_seen is
    preserved on refresh; meta keeps the full snapshot as JSON."""
    now = _now()
    rows = []
    for track in tracks:
        song_id = track.get("id") or track.get("catalog_id") or track.get("relationship_id")
        if not song_id:
            continue
        artist = track.get("artist") or ", ".join(track.get("artists") or [])
        rows.append((
            source, song_id, track.get("isrc"), track.get("name"), artist,
            track.get("album"), track.get("duration_ms"),
            json.dumps(track, ensure_ascii=False), now, now,
        ))
    if rows:
        conn.executemany(UPSERT, rows)
        conn.commit()
    return len(rows)


def get_links(conn, target, spotify_ids):
    """{spotify_id: target_id} for previously matched tracks."""
    out = {}
    ids = [i for i in spotify_ids if i]
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT spotify_id, target_id FROM links WHERE target = ? AND spotify_id IN ({marks})",
            [target, *chunk],
        )
        out.update(dict(rows.fetchall()))
    return out


def set_links(conn, target, mapping):
    # ponytail: links are trusted forever; delete a row to force re-resolution
    # if a linked id ever goes stale (e.g. a regional catalog pull).
    rows = [(sid, target, tid, _now()) for sid, tid in mapping.items() if sid and tid]
    if rows:
        conn.executemany("INSERT OR REPLACE INTO links VALUES (?, ?, ?, ?)", rows)
        conn.commit()


def get_state(conn, pair, target):
    return conn.execute(
        "SELECT snapshot_id, target_count FROM sync_state WHERE pair = ? AND target = ?", (pair, target)
    ).fetchone()


def set_state(conn, pair, target, snapshot_id, target_count):
    conn.execute(
        "INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?, ?, ?)",
        (pair, target, snapshot_id, target_count, _now()),
    )
    conn.commit()


def _in_chunks(conn, sql, prefix, ids):
    out = {}
    ids = [i for i in ids if i]
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(sql.format(marks=marks), [*prefix, *chunk])
        out.update(dict(rows.fetchall()))
    return out


def get_reverse_links(conn, target, target_ids):
    """{target_id: spotify_id} — the inverse of get_links, so a non-Spotify
    track can be traced back to its canonical Spotify identity."""
    return _in_chunks(
        conn, "SELECT target_id, spotify_id FROM links WHERE target = ? AND target_id IN ({marks})",
        [target], target_ids)


def get_isrcs(conn, source, ids):
    """{id: isrc} from the songs archive for a source (only rows that have one)."""
    got = _in_chunks(
        conn, "SELECT id, isrc FROM songs WHERE source = ? AND isrc IS NOT NULL AND id IN ({marks})",
        [source], ids)
    return {k: v for k, v in got.items() if v}


ORDER_HISTORY_KEEP = 12


def record_order(conn, playlist, source, entries):
    """Append one ordered snapshot ([[track_id, name, artist], ...]) of a
    provider's playlist — skipped when identical to the newest stored one, and
    pruned to the last ORDER_HISTORY_KEEP per (playlist, source)."""
    payload = json.dumps(entries, ensure_ascii=False)
    last = conn.execute(
        "SELECT tracks FROM playlist_order WHERE playlist = ? AND source = ? "
        "ORDER BY captured_at DESC LIMIT 1", (playlist, source)).fetchone()
    if last and last[0] == payload:
        return
    conn.execute("INSERT OR REPLACE INTO playlist_order VALUES (?, ?, ?, ?)",
                 (playlist, source, _now(), payload))
    conn.execute(
        "DELETE FROM playlist_order WHERE playlist = ? AND source = ? AND captured_at NOT IN ("
        "SELECT captured_at FROM playlist_order WHERE playlist = ? AND source = ? "
        "ORDER BY captured_at DESC LIMIT ?)",
        (playlist, source, playlist, source, ORDER_HISTORY_KEEP))
    conn.commit()


def get_order_history(conn, playlist, source):
    """[(captured_at, [[track_id, name, artist], ...]), ...] newest first."""
    rows = conn.execute(
        "SELECT captured_at, tracks FROM playlist_order WHERE playlist = ? AND source = ? "
        "ORDER BY captured_at DESC", (playlist, source))
    return [(ts, json.loads(t)) for ts, t in rows.fetchall()]


def get_playlist_state(conn, playlist, source):
    rows = conn.execute("SELECT canonical_id FROM playlist_state WHERE playlist = ? AND source = ?",
                        (playlist, source))
    return {r[0] for r in rows.fetchall()}


def set_playlist_state(conn, playlist, source, canonical_ids):
    """Replace one provider's stored membership (only after a clean pass)."""
    conn.execute("DELETE FROM playlist_state WHERE playlist = ? AND source = ?", (playlist, source))
    conn.executemany("INSERT OR IGNORE INTO playlist_state VALUES (?, ?, ?)",
                     [(playlist, source, cid) for cid in canonical_ids])
    conn.commit()
