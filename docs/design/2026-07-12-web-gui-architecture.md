# Web GUI — Self-Hosted Playlist Sync (Soundiiz-style) — Architecture

**Status:** DRAFT — revised after an independent architecture review (see §0).
**Date:** 2026-07-12
**Scope:** All 3 phases, composable design. Phase 1 is built first; 2 and 3 layer on additively.

---

## 0. Review outcome (honest state)

An independent architecture review red-teamed the first draft against the real engine code. Verdict: the **layered, composable structure is sound** (`web → services → engine`, one-way dependency, phases that add rather than rewrite), but the first draft **oversold the engine-change surface** as "trivial / one-line / byte-for-byte." Three concrete bugs and several underestimates were found and are now corrected below:

- **The `mirror_pair` source generalization is not one param** — non-Spotify sources have different track/playlist dict shapes and crash `compute_diff`. Real fix in §3.6.
- **`load_dotenv(override=True)` clobbers wizard-saved credentials** — deterministic Phase-1 bug. Fix in §3.3.
- **The logs→SSE sink crosses a thread→event-loop boundary** unsafely. Fix in §3.4.
- **Pairing is not one seam** — `reconcile` derives its state key internally. Fix in §3.8.
- **`run_pass` hard-requires Spotify** — blocks non-Spotify-anchored sync/transfer. Tracked in §8/§12.

Net: the architecture holds; the engine touch-list in §8 is now complete and honestly risk-rated. Every change still **defaults to today's behavior** — the existing Spotify→Apple/YTMusic path is unchanged unless a new code path opts in.

---

## 1. Goal & framing

Turn the headless sync engine into a self-hosted, Soundiiz-style web app:

- **Live sync visualization** — watch a pass happen in the browser.
- **In-UI account setup** — connect each service without hand-editing `.env`, OAuth where feasible.
- **Modern, UX-friendly layout** — for non-technical users.
- **North star:** connect any service, browse playlists, sync (one-way / N-way), and run one-off transfers with conflict resolution.

The engine already does the hard part: a provider-agnostic `MirrorTarget` protocol, ISRC-first matching, one-way `mirror_pair` + N-way `reconcile`, a SQLite song/link archive, and safety rails. This design **wraps that core**.

### Design tenets

1. **The engine stays the core; changes are additive and default-preserving.** New capability is added *around* it (services + web). Engine edits never change the existing Spotify→Apple/YTMusic behavior unless a new path opts in. Some edits (transfers, pairing) are real design work — enumerated honestly in §8 — not one-liners.
2. **Three layers, one dependency direction:** `web → services → engine`. The engine never imports the web/accounts tiers; services never import FastAPI. (Corollary: shared types the engine emits, like `Event`, live in a leaf engine module — §3.4.)
3. **Every service is headless-testable.** `SyncService`, `AccountService`, `TransferService` run from a test or the CLI with no browser.
4. **Composability = one seam per concern.** Adding a *service* = one connector + one provider + one registry line. Adding a *capability* = one service + one router + one page, plus at most one isolated engine hook.

---

## 2. Layered architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser  —  HTMX + Alpine.js + Tailwind (Play CDN), SSE client    │
└───────────────▲──────────────────────────────────▲────────────────┘
        HTTP (HTML + JSON)                    SSE (live events)
┌───────────────┴──────────────────────────────────┴────────────────┐
│  web/  (FastAPI, thin)                                              │
│  routers: accounts · sync · playlists · transfers · events         │
│  templates + static; app factory starts the scheduler on boot      │
└───────────────▲──────────────────────────────────▲────────────────┘
                │ calls                             │ subscribes
┌───────────────┴───────────────────────┐  ┌───────┴────────────────┐
│  Platform services (web-agnostic)      │  │  EventBus (pub/sub)     │
│  · SettingsStore   owns the env file   │  │  captures event loop;   │
│  · AccountService  connect / status    │◄─┤  logs sink marshals via │
│  · SyncService     ONE lock/queue for  │  │  call_soon_threadsafe   │
│                    ALL engine runs     │  └────────────────────────┘
│  · PlaylistService browse·pairing (P2) │
│  · TransferService A→B·conflicts (P3)  │
└───────────────▲────────────────────────┘
                │ uses (engine unchanged except the hooks in §8)
┌───────────────┴────────────────────────────────────────────────────┐
│  Engine (existing)                                                   │
│  targets/  MirrorTarget protocol · registry · mirror_pair · reconcile│
│  matching · archive (SQLite) · spotify · downloads · jellyfin · logs │
└──────────────────────────────────────────────────────────────────────┘
```

### Module layout

```
omni_sync/
  targets/            # ENGINE: MirrorTarget + providers + reconcile
                      #   + source-normalization & playlist accessors (P3, §3.6)
                      #   + build_one() registry fn (§3.6)
  matching.py archive.py spotify.py downloads.py jellyfin.py
  logs.py             # + leaf Event dataclass + optional set_sink (P1, §3.4)
  runner.py           # run_pass returns PassSummary; Spotify made optional (§3.5/§8)
  config.py cli.py    # headless CLI stays fully working

  settings.py         # NEW  SettingsStore: settings.json -> managed env file
  events.py           # NEW  EventBus (imports Event from logs); thread→loop bridge
  sync_service.py     # NEW  scheduler + run-now + ONE shared lock/queue
  accounts/           # NEW  Connector protocol + per-service connectors + registry
    __init__.py base.py spotify.py ytmusic.py apple.py jellyfin.py
  playlists.py        # NEW (P2)  browse + PlaylistLink pairing (explicit link_key)
  transfers.py        # NEW (P3)  transfers (reuses factored run-glue) + conflicts

  web/                # NEW  FastAPI (HTTP/SSE only)
    __init__.py routers/ templates/ static/
```

Dependency rule enforced by layout: `web/` → services → engine. `Event` lives in `logs.py` (a leaf), so `events.py` importing it is still services→engine, never the reverse.

---

## 3. Core abstractions (the composable seams)

### 3.1 Provider protocol — `MirrorTarget` (exists; extended additively in P3)

`targets/base.py` defines the contract: `list_playlists`, `is_editable`, `create`, `playlist_tracks`, `track_id`, `resolve`, `add`, `remove`, plus optional `prefetch`/`native_isrc_map`/`expected_ids`. It's symmetric enough to act as **source or destination** (reads via `playlist_tracks`, writes via `add`/`remove`).

**P1/P2:** unchanged. **P3** adds two small accessors (see §3.6) — `playlist_name(pl)` / `playlist_description(pl)` — because provider playlist dicts store the name differently (Apple `attributes.name`, YTMusic `title`, Spotify `name`), and transfers need the source playlist's name for create + live-feed labels.

### 3.2 Connector protocol — `accounts/base.py` (new)

The engine uses a service once tokens exist; it doesn't obtain them. That's a separate seam so "connect any service" is uniform.

```python
AuthKind = Literal["oauth_redirect", "oauth_device", "token_paste", "api_key"]

@dataclass
class Field: key:str; label:str; secret:bool=False; help:str=""; required:bool=True

@dataclass
class ConnStatus:
    state: Literal["connected","expired","unconfigured","error"]; detail: str = ""

class Connector(Protocol):
    id: str; name: str; auth_kind: AuthKind
    config_fields: list[Field]                 # app config the user supplies first
    def status(self) -> ConnStatus: ...
    def begin_redirect(self, redirect_uri:str) -> str: ...      # oauth_redirect
    def complete_redirect(self, params:dict) -> ConnStatus: ...
    def begin_device(self) -> DeviceCode: ...                   # oauth_device
    def poll_device(self, dc:DeviceCode) -> ConnStatus: ...
    def submit(self, values:dict) -> ConnStatus: ...            # token_paste / api_key
```

Registry `accounts/__init__.py: CONNECTORS = {id: Connector}` mirrors the targets registry.

| Service | `auth_kind` | User supplies first | Button then does |
|---|---|---|---|
| Spotify | `oauth_redirect` | client id/secret (own app) | redirect → callback → token cache |
| YouTube Music | `oauth_device` | Google OAuth client id/secret | show code+URL → poll → write oauth json |
| Apple Music | `token_paste` | — | validate bearer+user token vs amp-api → store |
| Jellyfin | `api_key` | url, user id | validate api key → store |

**Honesty note:** Spotify/YouTube require the user to register *their own* developer app (self-hosting can't reuse Soundiiz's). The wizard guides that (exact redirect URI, screenshots) and automates only the handshake.

### 3.3 SettingsStore — `settings.py` (new) — **corrected**

`data/settings.json` (git-ignored) is the single UI source of truth for all managed config (provider app credentials, sync options, download/Jellyfin options).

**How the engine sees changes (the fix):** the engine reads `os.getenv(...)` and `run_pass` reloads env each pass with `load_dotenv(override=True)` (runner.py:105) — designed to "pick up re-captured tokens without a restart." `override=True` makes the **file win over `os.environ`**, so projecting into `os.environ` alone is silently clobbered by a stale root `.env`. Therefore:

- **SettingsStore owns the env file the engine loads.** On every save it regenerates a managed env file under `data/` (e.g. `data/app.env`) from `settings.json`, and the web deployment points the engine's dotenv at that file. Now `load_dotenv(override=True)` picks up the **new** values — consistent with the engine's existing intent. The hand-edited root `.env` is not the loaded file in the web deployment (documented).
- **Env changes apply only at pass boundaries.** SyncService's single lock (§3.5) guarantees no save mutates env mid-pass, closing the "Apple reads two env vars separately" race.
- Token artifacts the SDKs manage (`.cache`, `ytmusic_oauth.json`) stay as files under `data/`; settings.json holds their paths, not contents. Secrets are never logged.

### 3.4 EventBus + live visualization — `logs.py` hook + `events.py` (new) — **corrected**

Every progress call already flows through one chokepoint: `log_add`/`log_remove`/`log_hold`/`log_miss`/`log_note`/`log_warn`/`log_summary`/`log_section` are distinct functions (logs.py:57-86), so a sink builds a typed `Event` **from which function fired** — no prose parsing.

**Layering + no circular import:** the `Event` dataclass lives in **`logs.py`** (a leaf module with no engine deps). `events.py` (services tier) imports `Event` from `logs.py` — direction stays services→engine.

```python
# logs.py  (leaf; default None = today's behavior exactly)
@dataclass(frozen=True)
class Event:
    ts: float; kind: str      # add|remove|hold|miss|note|warn|summary|section|lifecycle
    tag: str; message: str; data: dict | None = None
_sink: Callable[[Event], None] | None = None
def set_sink(fn): global _sink; _sink = fn
```

**Thread→loop bridge (the fix):** one-way passes run **one thread per target** (runner.py:188) — Apple and YTMusic call `log_*` concurrently. `asyncio.Queue` is *not* thread-safe. So:

- `EventBus` captures `asyncio.get_running_loop()` at startup.
- The sink (invoked from worker threads) marshals every publish via `loop.call_soon_threadsafe(queue.put_nowait, event)`.
- Per-client queues + a small ring buffer (last N events) so a late/reconnecting browser backfills the current pass.

The `events` router streams `Event`s over SSE (`text/event-stream`). Browser renders a live per-service feed + running `+added / −removed / ~held / ×missing` counters + per-pass summary.

### 3.5 SyncService — `sync_service.py` (new) — **corrected**

Owns the runtime the bare docker loop used to own, and is the **single serialization point for all engine invocations**:

- **One shared lock / job queue for BOTH scheduled syncs AND ad-hoc transfers.** The engine's on-disk resolve caches (runner.py:33-46, full-file load-then-overwrite) and the shared `song_cache.db` are not safe under concurrent writers, so every `run_pass`/transfer goes through the same queue — never two at once. This is stated as a hard invariant, not left to per-service implementation.
- **Scheduler:** one background task; sleeps `interval`, enqueues a pass. Replaces `cli.main`'s loop for the server path (the CLI loop stays for headless).
- **run_now():** enqueues an immediate pass, coalesced ("already running" is a no-op).
- Runs the (blocking, threaded) pass in a worker thread so the event loop stays responsive; emits `pass:queued/started/finished/failed` lifecycle events.

**Unified summary (engine hook):** `run_pass` currently discards its aggregates (runner.py:177 built for logging only, then dropped) and the CLI ignores the return (cli.py:19) — so returning a value is non-breaking. But the shape needs unifying across three paths: the one-way `agg` (name/pairs/added/removed/missing/held/skipped/created), the N-way path which **currently accumulates nothing** (each `reconcile` return is discarded at runner.py:280), and the `refresh_local` early return. Define one `PassSummary`:

```python
PassSummary = { pass_id, mode, started, duration_s, execute, ok, error,
                per_target: [ {name, added, removed, missing, held, deferred,
                               created, skipped} ] }
```

`_run_nway` must accumulate per-peer stats from each `reconcile` call — a small additive change.

### 3.6 TransferService — `transfers.py` (new, Phase 3) — **corrected (was oversold)**

One-off "copy playlist A→B." Reuses the engine, but the first draft's "one `source_key` param" was wrong. The real, still-isolated, still-default-preserving work:

1. **Source normalization.** `compute_diff`/`spotify_track_keys`/`protect_removals` (matching.py) assume a Spotify-shaped source (`artists` list, `added_at`, `isrc`, `id`); Apple has singular `artist`+no `id`, YTMusic has no `added_at`, and a Spotify *target* dict has `artists` not `artist`. `reconcile` already solves this with `_normalize` (base.py:187-200, cross-derives `artists`/`artist`, `added_at or ""`). **Extract `_normalize` and apply it at the top of the one-way path** so `mirror_pair`/`compute_diff` accept any source shape. Existing Spotify input already satisfies it → behavior unchanged.
2. **`source_key` param** on `mirror_pair` (default `"spotify"`) for the archive upsert (base.py:98) and link keying — so links/`expected_ids` are keyed by the actual source.
3. **Playlist accessors** `playlist_name(pl)`/`playlist_description(pl)` on `MirrorTarget` (§3.1) — else `create` builds an empty-named destination and the live feed shows `"?"` for Apple/YT sources.
4. **`build_one(provider_id, opts, sp)`** in `targets/__init__.py` — today only `build_targets`/`build_peers` exist; TransferService needs to construct exactly one named provider without reaching into `_REGISTRY`.
5. **Factor `run_target`'s glue** (create-if-missing runner.py:61-70, `is_editable` 81-83, cache load/save lifecycle 54/99-100) into a shared helper both `run_target` and TransferService call — otherwise transfers silently skip those safety steps.

`TransferJob = {source:(provider,playlist), dests:[provider…], dest_name, mode: copy|mirror, options}`. `copy` = adds only; `mirror` = adds + guarded removes. Progress rides the same EventBus, scoped by `job_id` in `Event.data`.

This is bounded to Phase 3 and additive (Spotify-source path unchanged), but it is **five real edits, not one** — reflected in §8.

### 3.7 Conflict resolution (Phase 3) — reuses the resolution cache

The matcher already yields the conflict classes: `not_found` (no match) and `held` (removal skipped for safety). No new engine machinery:

- The web surfaces a **review queue** of `not_found` tracks per job/playlist.
- A search box calls the provider's own search (the code `resolve` uses); "accept" writes `cache["search"][track_key] = chosen_id` — **the exact cache the engine reads next pass** — plus an optional archive link.
- `held` items show as informational with a one-click "remove anyway" that lifts the per-item cap.

Resolved conflicts "stick" because they write into the same seam the engine already consults.

### 3.8 PlaylistService + pairing (Phase 2) — `playlists.py` — **corrected**

- **Browse:** list any connected provider's playlists via `list_playlists`; show cross-provider match status.
- **Pairing model** (optional; today pairing is implicit by casefolded name):

```python
@dataclass
class PlaylistLink:
    id:str; name:str; members: dict[str,str|None]   # provider -> playlist_id (None="create")
    direction: Literal["oneway","nway"]; source:str|None; enabled:bool=True
```

- **Not one seam — an explicit `link_key` must be threaded through.** Name-keying appears in ≥4 places: playlist selection (runner.py:111-112), `run_target`'s match + archive state key (runner.py:58-59/74/94), `_run_nway`'s match (runner.py:254), and **inside `reconcile` itself**, which re-derives `key = name.casefold()` from its `name` arg (base.py:265) to address the canonical-snapshot table. So Phase 2 adds an explicit `link_key` parameter (default = `name.casefold()`, preserving today) threaded into `run_target`/`reconcile`, distinct from the display `name` used for logging. This is a real signature change in the core, but small and default-preserving.

---

## 4. Web API surface

All under `/api`, JSON unless noted; pages are server-rendered HTML.

| Method | Path | Purpose | Phase |
|---|---|---|---|
| GET | `/` | dashboard (status, last pass, quick actions) | 1 |
| GET | `/api/accounts` | connectors + `ConnStatus` each | 1 |
| POST | `/api/accounts/{id}/config` | save app credentials | 1 |
| POST | `/api/accounts/{id}/connect` | begin flow (redirect/device/paste) | 1 |
| GET | `/oauth/{id}/callback` | OAuth redirect landing (Spotify) | 1 |
| POST | `/api/accounts/{id}/poll` | device-flow poll (YouTube) | 1 |
| DELETE | `/api/accounts/{id}` | disconnect | 1 |
| GET/PUT | `/api/settings` | read / update sync options | 1 |
| POST | `/api/sync/run` | run a pass now (`?execute=0/1`) | 1 |
| POST | `/api/sync/schedule` | pause/resume/interval | 1 |
| GET | `/api/sync/status` | running? next run? last summary | 1 |
| GET | `/events` | **SSE** live event stream | 1 |
| GET | `/api/playlists?provider=` | browse one provider | 2 |
| GET/PUT | `/api/links` | pairing links CRUD | 2 |
| POST | `/api/transfers` | start a one-off transfer job | 3 |
| GET | `/api/transfers/{id}` | job status + conflict queue | 3 |
| POST | `/api/transfers/{id}/resolve` | accept a match for a `not_found` track | 3 |

All engine-invoking routes (`/api/sync/run`, `/api/transfers`) enqueue onto SyncService's single queue (§3.5) — the one concurrency guard, on top of the engine's `MAX_ADDS`/`MAX_REMOVALS`/dry-run caps.

---

## 5. Key data flows

**Connect Spotify.** Wizard shows redirect URI to register → paste client id/secret (`POST /config`) → Connect (`POST /connect` → auth URL) → Spotify → `/oauth/spotify/callback` → spotipy exchanges code → token cache → `status()=connected`.

**Connect YouTube.** Save Google id/secret → Connect → `begin_device()` → show code+URL → poll `/poll` → `ytmusic_oauth.json` → connected.

**Run + watch a sync.** `POST /api/sync/run?execute=1` → enqueue → SyncService takes the lock, runs `run_pass` in a thread → logs sink → `call_soon_threadsafe` → EventBus → open `/events` SSE renders feed + counters → `PassSummary` returned → `pass:finished` → dashboard updates.

**Transfer A→B (P3).** `POST /api/transfers` → enqueue (same lock) → build source+dest via `build_one` → normalized `mirror_pair(dest, source.playlist_tracks(src), source_key=source.id)` → live feed scoped by `job_id` → `not_found` → conflict queue → resolve writes the resolution cache → re-run picks up accepted matches.

---

## 6. Frontend (no build step)

- **Stack:** HTMX (partials, form posts), Alpine.js (wizard steps, live counters), Tailwind via Play CDN, vendored minified JS under `static/` (self-contained, offline, LAN-friendly). No Node toolchain.
- **Pages:** Dashboard, Accounts/Wizard (per-service cards + status pills), Sync settings (mode, interval, caps, playlist picker), Live view (SSE feed + counters), Playlists browser (P2), Transfers (P3).
- **Escape hatch:** if P3's conflict tables outgrow HTMX, add Vite + a component island *then* — the API is already JSON, so a later SPA is additive.

---

## 7. Security posture (LAN-only, gap named)

Per decision: **LAN/localhost bind, no login** — an explicit, accepted trade-off, recorded per the backend-first-validation rule:

- **Named gap:** anyone on the LAN can trigger syncs/transfers and read stored credentials via the UI. No per-request auth.
- **Mitigations:** bind to LAN (documented "do not port-forward"); single queue caps concurrency; engine add/removal caps + dry-run default bound blast radius and API spend; secrets on disk under `data/` (as today).
- **Upgrade path (not built now):** a single-password gate — one env-set password, a session cookie, a dependency on the routes (~20–30 lines of **middleware**, not a refactor). Wire before any exposure beyond the LAN.
- **OAuth redirect URIs** point at the app's own `/oauth/{id}/callback` (host:port registered by the user). Documented in the wizard.

---

## 8. Engine changes required (complete, honestly risk-rated)

Every change **defaults to today's behavior**; the existing Spotify→Apple/YTMusic path is unaffected unless a new code path opts in.

| # | File | Change | Phase | Risk |
|---|---|---|---|---|
| 1 | `logs.py` | leaf `Event` dataclass + optional `set_sink`; each `log_*` emits an Event | 1 | low; default off |
| 2 | `events.py` (new) | EventBus captures loop; sink marshals via `call_soon_threadsafe` | 1 | **medium** — concurrency-sensitive, must be designed (thread→loop) |
| 3 | `runner.py` | `run_pass` returns unified `PassSummary`; `_run_nway` accumulates reconcile stats | 1 | low–medium; unify 3 paths |
| 4 | `settings.py` + env loading | SettingsStore owns the managed env file the engine reloads; `.env` can't clobber wizard creds | 1 | **medium** — deterministic bug if unaddressed (§3.3) |
| 5 | `targets/__init__.py` | add `build_one(provider_id, opts, sp)` | 3 (P2 may reuse) | low; additive registry fn |
| 6 | `targets/base.py` + `matching.py` | extract `_normalize` into the one-way path; add `source_key` param to `mirror_pair` | 3 | medium; isolated, default-preserving |
| 7 | `targets/base.py` (protocol) + providers | `playlist_name(pl)`/`playlist_description(pl)` accessors | 3 | low–medium; ~3 lines/provider |
| 8 | `runner.py` | factor `run_target`'s create/editable/cache glue into a shared helper | 3 | low; behavior-preserving refactor |
| 9 | `runner.py` + `base.py:265` | thread an explicit `link_key` (≠ display name) into `run_target`/`reconcile` | 2 | medium; touches reconcile's state key |
| 10 | `runner.py:108` | make Spotify optional when `providers` excludes it | 2/3 | medium; reorder required-env checks + `selected` derivation |

Matching, reconcile logic, caches, safety rails, and the providers' resolve/add/remove stay untouched.

---

## 9. Phase breakdown (each ships independently, composes without rewrites)

**Phase 1 — Platform + control panel.** SettingsStore (managed env), EventBus + `logs` sink (thread-safe), SyncService (schedule/run-now/**single queue**), Connector layer + OAuth (Spotify redirect, YouTube device) + Apple/Jellyfin forms, web app (dashboard, wizard, live view, sync settings, playlist on/off). Container entrypoint → uvicorn. Engine hooks: #1–#4. **Delivers all three explicit asks.**

**Phase 2 — Playlist browser + pairing.** PlaylistService (browse), `PlaylistLink` + override UI, per-playlist direction. Engine hooks: #9 (explicit `link_key`), begins #10 (Spotify optional), may reuse #5. No rewrite of Phase 1.

**Phase 3 — On-demand transfers + conflict resolution.** TransferService (source normalization + accessors + `build_one` + factored glue: #5–#8), transfers UI, conflict review queue writing the resolution cache. Reuses the entire engine + Phase-1 event/live infra.

Composition proof: each phase adds *services + routers + pages* plus isolated, default-preserving engine hooks. No phase reverses the `web → services → engine` direction or rewrites a prior phase.

---

## 10. Testing strategy

- **Engine hooks:** `logs.set_sink` emits events matching log calls; `run_pass` `PassSummary` shape across one-way/N-way/refresh-local; `mirror_pair(source_key="spotify")` byte-equal to today; `mirror_pair` with an Apple/YT-shaped source (the case that currently `KeyError`s) resolves cleanly after normalization; `build_one` returns the right provider.
- **Services (headless):** SettingsStore round-trip + managed-env regeneration (assert a wizard save survives a `run_pass` reload — the clobber regression test); SyncService single-queue serialization (a transfer can't interleave a scheduled pass); EventBus thread→loop marshaling (publish from a worker thread, client receives); each `Connector.status()` with faked tokens; TransferService against fake providers; conflict-resolve writes the cache.
- **Web:** FastAPI `TestClient` for routers; one SSE smoke test.
- **Regression:** existing `test_matching.py` / `test_reconcile.py` stay green (engine untouched on the Spotify path).

---

## 11. Deployment

- `Dockerfile` CMD → `uvicorn omni_sync.web:app --host 0.0.0.0 --port 8080` (LAN bind).
- `docker-compose.yml`: expose `8080`; keep `./data` + music volumes; point the engine's dotenv at the managed `data/app.env`; drop the bare-loop command (SyncService owns scheduling).
- Headless CLI (`python -m omni_sync --loop`) remains for non-web users.

---

## 12. Risks & open questions

- **Spotify currently mandatory** (runner.py:108 runs `spotify.client()` + derives `selected` before any provider branching). Fine for Phase-1 (Spotify-source mirroring); **must relax (#10)** for non-Spotify-anchored N-way/transfers, or the "connect any service" north star is only partly true.
- **`archive.links.spotify_id`** column is literally named; a generalized source writes non-Spotify IDs under it. Harmless in practice (ID spaces don't collide) but a semantic/auditability drift to note — not a schema migration for now.
- **Env-as-IPC race** (Apple reads two vars separately, apple.py:22-27) — resolved by applying env changes only at pass boundaries under the single queue (§3.3/§3.5).
- **Spotify/Google app registration** is unavoidable for self-hosting; wizard UX is the biggest remaining risk. Apple token expiry surfaces via `ConnStatus.state="expired"` + a reconnect path.
- **Long passes vs SSE reconnects** — ring-buffer backfill covers a mid-pass refresh.
- Open: persist per-pass history (small table) in Phase 1 or defer? Leaning defer — Phase 1 shows current/last from the ring buffer + `PassSummary`.
```
