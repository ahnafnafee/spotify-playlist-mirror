// Shared types mirroring the FastAPI backend's JSON contract exactly
// (spotify_mirror/web/routers/*.py + spotify_mirror/accounts/*.py). Keep this
// file in sync with the backend if the contract ever changes shape.

export type AuthKind = 'oauth_redirect' | 'oauth_device' | 'token_paste' | 'api_key'

export type AccountState = 'connected' | 'expired' | 'unconfigured' | 'error'

export interface AccountField {
  key: string
  label: string
  secret: boolean
  help: string
  required: boolean
}

export interface Account {
  id: string
  name: string
  auth_kind: AuthKind
  fields: AccountField[]
  state: AccountState
  detail: string | null
}

/** Shared shape for the plain `{ok: true}` acks (config save, settings save,
 * disconnect). */
export interface OkResponse {
  ok: true
}

export interface ConnectRedirectResponse {
  kind: 'redirect'
  url: string
  redirect_uri: string
}

export interface ConnectDeviceResponse {
  kind: 'device'
  user_code: string
  verification_url: string
  device_code: string
  interval: number
}

/** token_paste / api_key connect responses submit values directly and get a
 * status back instead of a redirect/device hand-off. */
export interface ConnectDirectResponse {
  kind: 'token_paste' | 'api_key'
  state: AccountState
  detail: string | null
}

export type ConnectResponse = ConnectRedirectResponse | ConnectDeviceResponse | ConnectDirectResponse

export interface PollResponse {
  state: AccountState
  detail: string | null
}

/** GET/PUT /api/settings — arbitrary KEY:value config; secrets are masked out
 * server-side and never round-tripped to the browser. */
export type Settings = Record<string, string>

export interface TargetSummary {
  name: string
  added: number
  removed: number
  missing: number
  held: number
  deferred: number
  created: number
  skipped: number
}

export interface PassSummary {
  mode: string
  execute: boolean
  duration_s: number
  ok: boolean
  error: string | null
  per_target: TargetSummary[]
}

export interface SyncStatus {
  running: boolean
  scheduled: boolean
  interval_s: number
  last: PassSummary | null
  /** Epoch seconds of the next scheduled pass, or `null` when auto-sync is
   * paused (or nothing has ever been scheduled). */
  next_run_at?: number | null
  /** While a pass runs: "preview" (dry run — checks everything, changes
   * nothing) or "execute" (a real sync); null/absent when idle. */
  mode?: 'preview' | 'execute' | null
}

export interface RunResponse {
  queued: true
}

export interface ScheduleRequest {
  interval?: string
  action?: 'pause' | 'resume'
}

/** One line of the live SSE feed. `data` carries kind-specific extras (e.g.
 * `{dry: boolean}` for add/remove, `{detail: string}` for section) that the
 * UI doesn't need to render today but may display opportunistically. */
export type EventKind = 'add' | 'remove' | 'hold' | 'miss' | 'download' | 'note' | 'warn' | 'summary' | 'section'

export interface SyncEvent {
  ts: number
  kind: EventKind
  tag: string
  message: string
  data?: Record<string, unknown> | null
}

/** GET /api/playlists?provider=<id> — one entry per playlist on that service.
 * `image` is a cover-art URL and may be an empty string (no art available).
 * `count` is `null` when the service doesn't expose a track count cheaply
 * (Apple Music) — never render the literal "null", see formatTrackCount().
 * `owned` is Spotify-only: `false` marks a playlist the connected user
 * follows but doesn't own, which Spotify's API refuses to return tracks for
 * (403) — treat a missing/undefined value as owned; other providers never
 * send this field. */
export interface ProviderPlaylist {
  id: string
  name: string
  count: number | null
  image: string
  owned?: boolean
}

export type LinkDirection = 'oneway' | 'nway'

/** Provider id -> playlist id, or `null` to create a new same-named playlist
 * on that service. A provider absent from this map isn't part of the link. */
export type LinkMembers = Record<string, string | null>

/** GET /api/links entry — an explicit cross-service playlist pairing (for
 * playlists that don't share a name, or to scope a sync to specific
 * services). */
export interface PlaylistLink {
  id: string
  name: string
  members: LinkMembers
  direction: LinkDirection
  source: string | null
  enabled: boolean
}

/** PUT /api/links body — omit `id` to create a new link; include it to
 * update an existing one. */
export interface LinkUpsertRequest {
  id?: string
  name: string
  members: LinkMembers
  direction: LinkDirection
  source: string | null
  enabled: boolean
}

export type TransferStatus = 'queued' | 'running' | 'done' | 'error' | 'busy'

export interface TransferEndpoint {
  provider: string
  playlist_id: string
  playlist_name: string
}

/** A destination track that couldn't be automatically matched during a
 * transfer, awaiting a manually pasted match. */
export interface TransferConflict {
  key: string
  name: string
  artist: string
  resolved: boolean
}

/** GET /api/transfers/{id} — a one-off "copy playlist A -> B" job. */
export interface TransferJob {
  id: string
  status: TransferStatus
  source: TransferEndpoint
  dest: TransferEndpoint
  added: number
  deferred: number
  conflicts: TransferConflict[]
  error: string | null
}

/** POST /api/transfers body. `dest_playlist_id: null` creates a new playlist
 * named `dest_name` on the destination instead of copying into an existing
 * one. */
export interface StartTransferRequest {
  source_provider: string
  source_playlist_id: string
  dest_provider: string
  dest_playlist_id: string | null
  dest_name: string
}

export interface StartTransferResponse {
  job_id: string
}

export interface ResolveConflictRequest {
  key: string
  dest_id: string
}
