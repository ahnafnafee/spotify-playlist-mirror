"""Mirror targets: the services a playlist is mirrored across.

Adding a provider (Deezer, Tidal, …) is deliberately local:
  1. Write `targets/<svc>.py` with a `MirrorTarget` subclass implementing the
     ~8 methods (see base.py). Carry ISRC in `playlist_tracks` if the API has
     it — that's what makes cross-provider matching reliable and free.
  2. Add one line to `_REGISTRY` below: `source -> builder(opts, sp) -> target|None`.
Everything else — one-way mirroring, N-way reconcile, canonical identity,
caching, safety rails — is provider-agnostic and needs no change.
"""

from .apple import AppleMusicTarget
from .base import MirrorTarget, TargetAuthError, mirror_pair, reconcile
from .spotify_target import SpotifyTarget
from . import ytmusic

__all__ = ["AppleMusicTarget", "SpotifyTarget", "MirrorTarget", "TargetAuthError",
           "mirror_pair", "reconcile", "build_targets", "build_peers", "build_one", "is_peer"]


def _apple(opts):
    from ..config import required_env
    from ..logs import log_note
    try:
        required_env("APPLE_BEARER_TOKEN")
        required_env("APPLE_USER_TOKEN")
        return AppleMusicTarget(opts.storefront, opts.cache_file)
    except RuntimeError as e:
        log_note(f"Apple Music skipped: {e}", tag="apple")
        return None


# source -> builder(opts, sp) -> a ready MirrorTarget, or None when unconfigured.
# Order matters: ISRC-rich providers first so they seed cross-provider identity.
# `sp` (the Spotify client) is only needed by peers that read/write Spotify.
_REGISTRY = {
    "spotify": lambda opts, sp: SpotifyTarget(sp, opts.spotify_cache_file) if sp is not None else None,
    "apple": lambda opts, sp: _apple(opts),
    "ytmusic": lambda opts, sp: ytmusic.build(),
}
_SOURCE_ORDER = ["spotify", "apple", "ytmusic"]


def build_targets(opts, sp=None):
    """One-way mirror targets this run: every opted-in provider except the source
    (opts.sync_source). An empty opts.providers means every configured provider
    (the same 'empty = all' convention as opts.playlists, and what the UI shows).
    `sp` (the Spotify client) is only needed when the source is a non-Spotify
    provider, so Spotify itself becomes a writable target."""
    source = getattr(opts, "sync_source", None) or "spotify"
    wanted = {s.strip() for s in (opts.providers or "").split(",") if s.strip()}
    return [t for src in _SOURCE_ORDER if src != source and (not wanted or src in wanted)
            for t in (_REGISTRY[src](opts, sp),) if t]


def build_one(provider_id, opts, sp=None):
    """Construct a single provider by id (None if unknown/unconfigured). Used by
    the web layer to browse or transfer one specific service."""
    builder = _REGISTRY.get(provider_id)
    return builder(opts, sp) if builder else None


def is_peer(provider_id):
    """Whether a provider is a sync/transfer peer — i.e. has a MirrorTarget that
    can read and write tracks. False for browse/output-only services like
    Jellyfin, which the download mirror feeds instead of track-level writes."""
    return provider_id in _REGISTRY


def build_peers(opts, sp):
    """N-way peer nodes, limited to opts.providers and to what's configured, in
    ISRC-rich-first order. An empty opts.providers means every configured provider
    (matching the UI, which shows every connected peer when none are explicitly
    chosen) — so a job saved without touching the Services step still syncs rather
    than silently finding zero peers. Needs the Spotify client for the Spotify peer."""
    wanted = {s.strip() for s in (opts.providers or "").split(",") if s.strip()}
    return [peer for src in _SOURCE_ORDER if not wanted or src in wanted
            for peer in (_REGISTRY[src](opts, sp),) if peer]
