"""Track identity and matching.

The hierarchy every cross-service music tool uses: a hard identifier (ISRC or a
cached link) first, then a fuzzy search scored against title/artist/duration.
The fuzzy layer is RapidFuzz `token_set_ratio` (order/subset/decoration
tolerant) plus Jaro-Winkler (short strings, transliteration near-misses), run
over both raw and romanized (anyascii) variants so different scripts match. The
duration anchor gates the looser matching so a different version or a
wrong-artist cover is rejected when its length disagrees.
"""

import re
import unicodedata

from anyascii import anyascii
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

FUZZY_THRESHOLD = 0.92
DURATION_TOLERANCE_MS = 2500

PAREN_FEAT_RE = re.compile(r"[\(\[]\s*(feat|featuring|ft|with)\b.*?[\)\]]", re.IGNORECASE)
TRAILING_FEAT_RE = re.compile(r"\s+(feat|featuring|ft)\s+.*$")


def normalize_text(value):
    """Unicode-aware: keeps letters/digits in ANY script (Cyrillic, CJK,
    Bengali, ...). A Latin-only character class silently empties non-Latin
    titles, which breaks matching and can delete real tracks."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    normalized = re.sub(r"[\W_]+", " ", normalized)
    return " ".join(normalized.split())


def loose_name(name):
    """Title with feat-clauses stripped — '(feat. X)' is the classic drift for
    the SAME song. Version qualifiers like (Live)/(Acoustic) are kept: those
    are different recordings — but the abbreviation 'ver' expands to 'version'
    so 'Twin Ver.' and 'Twin Version' agree token-for-token."""
    cleaned = TRAILING_FEAT_RE.sub("", normalize_text(PAREN_FEAT_RE.sub(" ", name or ""))).strip()
    cleaned = re.sub(r"\bver\b", "version", cleaned)
    return cleaned or normalize_text(name)


def romanized(text):
    """ASCII romanization for cross-script matching (Камин->kamin, ত্রি->tri).
    Cyrillic / Bengali / Greek / Arabic romanize reliably; CJK yields a Chinese
    reading, so kanji/kana titles stay best-effort."""
    return normalize_text(anyascii(str(text or "")))


def track_key(name, artist):
    return f"{loose_name(name)}|{normalize_text(artist)}"


def _sim_strict(a, b):
    """0..1 similarity that PENALIZES extra words (token_sort) with a
    Jaro-Winkler floor for short strings / transliteration near-misses. Rejects
    different versions whose titles carry extra words when duration can't."""
    if not a or not b:
        return 0.0
    return max(fuzz.token_sort_ratio(a, b) / 100.0, JaroWinkler.normalized_similarity(a, b))


def _sim_loose(a, b):
    """0..1 token-set similarity: order-, subset- and decoration-tolerant. High
    when one string's tokens are a subset of the other (multi-artist credits,
    decorated video titles). Trusted for titles only with duration support."""
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100.0


def _name_variants(text):
    return {v for v in (loose_name(text), romanized(text)) if v}


def _best(sim, q_variants, c_variants):
    return max((sim(a, b) for a in q_variants for b in c_variants if a and b), default=0.0)


def fuzzy_in(key, keys, threshold=FUZZY_THRESHOLD):
    # ponytail: O(len(keys)) scan per unmatched track; fine for playlist-sized
    # sets, index it if someone mirrors a 50k-track monster.
    return any(_sim_loose(key, k) >= threshold for k in keys)


def score_candidate(name, artists, duration_ms, cand_name, cand_artist, cand_duration_ms):
    """(score in 0..1, acceptable) for a search-result candidate vs the wanted
    track — the fuzzy fallback when no ISRC/link resolves it."""
    if isinstance(artists, str):
        artists = [artists]
    q_names, c_names = _name_variants(name), _name_variants(cand_name)
    name_strict = _best(_sim_strict, q_names, c_names)
    name_loose = _best(_sim_loose, q_names, c_names)

    joined = " ".join(artists)
    q_art = {normalize_text(joined), romanized(joined)}
    c_art = {normalize_text(cand_artist), romanized(cand_artist)}
    artist_sim = _best(_sim_loose, q_art, c_art)  # subset-tolerant: services list the primary artist

    if duration_ms is not None and cand_duration_ms is not None:
        delta = abs(duration_ms - cand_duration_ms)
        duration_score = max(0.0, 1.0 - delta / (DURATION_TOLERANCE_MS * 4))
        duration_close = delta <= DURATION_TOLERANCE_MS
    else:
        duration_score, duration_close = 0.5, False

    name_sim = max(name_strict, name_loose) if duration_close else name_strict
    score = 0.45 * name_sim + 0.35 * artist_sim + 0.20 * duration_score
    strong = duration_close and name_sim >= 0.78 and artist_sim >= 0.58
    fuzzy = name_strict >= 0.88 and artist_sim >= 0.60
    return score, (strong or fuzzy)


def spotify_track_keys(track):
    keys = {track_key(track["name"], artist) for artist in track["artists"]}
    keys.add(track_key(track["name"], " ".join(track["artists"])))
    return keys


def compute_diff(sp_tracks, target_tracks, expected_by_sp, target_id_of, threshold=FUZZY_THRESHOLD):
    """Set diff shared by every target.

    expected_by_sp: {spotify_track_id: set(target_ids)} the Spotify track is
    known to correspond to (cached links + ISRC candidates). target_id_of(t):
    the target's stable id for one of its existing tracks.

    to_add: Spotify tracks with no matching id and no exact title|artist key on
    the target, sorted by added_at ascending so the newest lands last.
    to_remove: target tracks whose id isn't expected and whose key has no exact
    or fuzzy Spotify match (fuzzy applies only to this destructive side, as the
    guard against a metadata mismatch deleting a real track).
    """
    target_ids = {target_id_of(t) for t in target_tracks if target_id_of(t)}
    target_keys = {track_key(t["name"], t["artist"]) for t in target_tracks}

    expected_all = set()
    sp_keys = set()
    to_add = []
    for tr in sp_tracks:
        expected = expected_by_sp.get(tr.get("id")) or set()
        expected_all |= expected
        keys = spotify_track_keys(tr)
        sp_keys |= keys
        if expected & target_ids:
            continue
        if keys & target_keys:
            continue
        to_add.append(tr)
    to_add.sort(key=lambda t: t["added_at"])  # ISO-8601 Z strings sort lexicographically

    to_remove = []
    for t in target_tracks:
        tid = target_id_of(t)
        if tid and tid in expected_all:
            continue
        key = track_key(t["name"], t["artist"])
        if key in sp_keys or fuzzy_in(key, sp_keys, threshold):
            continue
        to_remove.append(t)
    return to_add, to_remove


def protect_removals(to_remove, not_found_tracks, threshold=0.8):
    """Split removals into (safe, held): a target track resembling a Spotify
    track that has NO match on that service must not be deleted — that would
    drop the song with no replacement. Deliberately loose threshold: wrongly
    holding leaves an extra track; wrongly deleting loses music."""
    nf_keys = set()
    for track in not_found_tracks:
        nf_keys |= spotify_track_keys(track)
    safe, held = [], []
    for track in to_remove:
        key = track_key(track["name"], track["artist"])
        if key in nf_keys or fuzzy_in(key, nf_keys, threshold):
            held.append(track)
        else:
            safe.append(track)
    return safe, held
