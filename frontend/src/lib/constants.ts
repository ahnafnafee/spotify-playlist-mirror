import type { AccountState, EventKind, TransferStatus } from '../types'

interface StateStyle {
  label: string
  glyph: string
  badge: string
  text: string
}

/** connected→success · expired→warning · unconfigured→neutral · error→danger,
 * per the design spec's StatusPill map. Each pairs a mono glyph with the
 * word — color is never the only signal. */
export const ACCOUNT_STATE_STYLES: Record<AccountState, StateStyle> = {
  connected: { label: 'Connected', glyph: '✓', badge: 'bg-success-soft text-success', text: 'text-success' },
  expired: { label: 'Expired — reconnect', glyph: '~', badge: 'bg-warning-soft text-warning', text: 'text-warning' },
  error: { label: 'Error', glyph: '!', badge: 'bg-danger-soft text-danger', text: 'text-danger' },
  unconfigured: { label: 'Not configured', glyph: '·', badge: 'bg-neutral-soft text-neutral', text: 'text-neutral' },
}

export const TRANSFER_STATUS_STYLES: Record<TransferStatus, StateStyle> = {
  queued: { label: 'Queued', glyph: '·', badge: 'bg-neutral-soft text-neutral', text: 'text-neutral' },
  busy: { label: 'Waiting for the sync engine…', glyph: '~', badge: 'bg-warning-soft text-warning', text: 'text-warning' },
  running: { label: 'Running…', glyph: '…', badge: 'bg-accent-soft text-accent', text: 'text-accent' },
  done: { label: 'Done', glyph: '✓', badge: 'bg-success-soft text-success', text: 'text-success' },
  error: { label: 'Error', glyph: '!', badge: 'bg-danger-soft text-danger', text: 'text-danger' },
}

interface ServiceStyle {
  label: string
  dot: string
  soft: string
  text: string
}

/** Service identity — dots + soft-tinted badges only, never buttons (the app
 * accent is teal). Keyed by whichever string shows up: account ids (used in
 * API paths, e.g. "ytmusic") and live-feed event tags (e.g. "yt") don't
 * always match, plus two internal, non-service tags ("sync", "local"). */
const SERVICE_STYLES: Record<string, ServiceStyle> = {
  spotify: { label: 'Spotify', dot: 'bg-svc-spotify', soft: 'bg-svc-spotify-soft', text: 'text-svc-spotify' },
  apple: { label: 'Apple Music', dot: 'bg-svc-apple', soft: 'bg-svc-apple-soft', text: 'text-svc-apple' },
  yt: { label: 'YouTube Music', dot: 'bg-svc-ytmusic', soft: 'bg-svc-ytmusic-soft', text: 'text-svc-ytmusic' },
  ytmusic: { label: 'YouTube Music', dot: 'bg-svc-ytmusic', soft: 'bg-svc-ytmusic-soft', text: 'text-svc-ytmusic' },
  jellyfin: { label: 'Jellyfin', dot: 'bg-svc-jellyfin', soft: 'bg-svc-jellyfin-soft', text: 'text-svc-jellyfin' },
  sync: { label: 'Sync', dot: 'bg-accent', soft: 'bg-accent-soft', text: 'text-accent' },
  local: { label: 'Local files', dot: 'bg-info', soft: 'bg-info-soft', text: 'text-info' },
}
const DEFAULT_SERVICE_STYLE: ServiceStyle = {
  label: '',
  dot: 'bg-neutral',
  soft: 'bg-neutral-soft',
  text: 'text-neutral',
}

export function tagLabel(tag: string): string {
  return SERVICE_STYLES[tag]?.label || tag
}
export function tagDot(tag: string): string {
  return (SERVICE_STYLES[tag] ?? DEFAULT_SERVICE_STYLE).dot
}
export function tagSoft(tag: string): string {
  return (SERVICE_STYLES[tag] ?? DEFAULT_SERVICE_STYLE).soft
}
export function tagText(tag: string): string {
  return (SERVICE_STYLES[tag] ?? DEFAULT_SERVICE_STYLE).text
}

/** Provider id -> ServiceLogo id (both the "yt" event tag and the "ytmusic"
 * account id resolve to the same YouTube Music mark). */
export function serviceLogoId(idOrTag: string): 'spotify' | 'apple' | 'ytmusic' | 'jellyfin' | null {
  if (idOrTag === 'spotify') return 'spotify'
  if (idOrTag === 'apple') return 'apple'
  if (idOrTag === 'yt' || idOrTag === 'ytmusic') return 'ytmusic'
  if (idOrTag === 'jellyfin') return 'jellyfin'
  return null
}

interface KindStyle {
  /** Mono glyph shown in the 20px FeedRow tile. Fixed per the design spec:
   * add + · remove − · hold ~ · miss × · warn !. `note`/`download` are this
   * app's own additions (extra event kinds the design's 5-kind sample
   * doesn't cover) styled to the same grammar. */
  glyph: string
  tileBg: string
  tileText: string
  /** Message text color — miss rows dim relative to the rest. */
  text: string
  /** Extra classes for the whole row — used for kinds that deserve a
   * highlighted band (warnings, the pass-complete summary). */
  row?: string
}

export const KIND_STYLES: Record<EventKind, KindStyle> = {
  add: { glyph: '+', tileBg: 'bg-success-soft', tileText: 'text-success', text: 'text-text' },
  remove: { glyph: '−', tileBg: 'bg-danger-soft', tileText: 'text-danger', text: 'text-text' },
  hold: { glyph: '~', tileBg: 'bg-warning-soft', tileText: 'text-warning', text: 'text-text' },
  miss: { glyph: '×', tileBg: 'bg-neutral-soft', tileText: 'text-neutral', text: 'text-text-2' },
  download: { glyph: '↓', tileBg: 'bg-info-soft', tileText: 'text-info', text: 'text-text' },
  note: { glyph: '·', tileBg: 'bg-neutral-soft', tileText: 'text-neutral', text: 'text-text-2' },
  warn: {
    glyph: '!',
    tileBg: 'bg-warning-soft',
    tileText: 'text-warning',
    text: 'font-semibold text-text',
    row: 'bg-warning-soft/40',
  },
  summary: {
    glyph: '✓',
    tileBg: 'bg-accent-soft',
    tileText: 'text-accent',
    text: 'font-semibold text-text',
    row: 'bg-surface-2',
  },
  section: { glyph: '', tileBg: '', tileText: '', text: 'text-text-3' },
}

/** Why a `ProviderPlaylist` with `owned === false` (Spotify-only, see
 * types.ts) can be browsed but not copied — shown wherever that playlist is
 * tagged, so the explanation stays identical across surfaces. */
export const UNOWNED_PLAYLIST_REASON = "Spotify won't let apps copy playlists you don't own."

export const DOWNLOAD_FORMAT_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'Default (MP3)' },
  { value: 'mp3', label: 'MP3' },
  { value: 'flac', label: 'FLAC (lossless)' },
  { value: 'ogg', label: 'OGG Vorbis' },
  { value: 'opus', label: 'Opus (no re-encode from YouTube)' },
  { value: 'm4a', label: 'M4A / AAC' },
  { value: 'wav', label: 'WAV (uncompressed)' },
]
