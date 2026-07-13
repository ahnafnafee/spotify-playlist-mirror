# Web GUI — Phase 3 (On-Demand Transfers + Conflict Resolution) Implementation Plan

> REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** One-off "copy playlist A→B" across any two connected services, streamed live, with a review queue for tracks that couldn't be matched.

**Architecture:** A dedicated, ISOLATED `transfer()` engine function — it normalizes both sides via the existing `_normalize` and reuses each provider's `resolve`/`add`, so it never touches the safety-critical `mirror_pair` sync core. Transfers run through SyncService's single guard (serialized with scheduled syncs). Conflicts (`not_found`) are surfaced and resolved by writing the provider's resolution cache — the same seam the engine already reads.

**Design doc:** `docs/design/2026-07-12-web-gui-architecture.md` §3.6, §3.7, §9 Phase 3.

## Global Constraints

- Additive + default-preserving; `mirror_pair`/`reconcile` untouched. Full `uv run pytest` stays green.
- Layer rule holds; `_normalize` (engine) is reused by the transfer function.
- Transfers serialize with syncs (one engine writer at a time).
- Copy mode only in this phase (adds, never removes) — the safe, headline case. Mirror-with-removals is a follow-up.

---

## Task P3-1: playlist_name / playlist_description accessors (hook #7)

**Files:** `targets/base.py` (+ apple.py, ytmusic.py overrides); test in `tests/test_playlists.py`.
**Produces:** `MirrorTarget.playlist_name(pl)` / `playlist_description(pl)` — so a transfer can create the destination named after the source playlist regardless of provider dict shape.

- [ ] base defaults: `playlist_name` → `pl.get("name","")`; `playlist_description` → `pl.get("description","")`.
- [ ] apple override: name `pl["attributes"]["name"]`, desc `attributes.description.standard` (guard missing).
- [ ] ytmusic override: name `pl.get("title","")`.
- [ ] Test: each accessor returns the display name for a provider-shaped dict.

## Task P3-2: transfer() engine function

**Files:** Create `omni_sync/transfers.py`; `tests/test_transfers.py`.
**Produces:** `transfer(source, dest, src_pl, dest_pl, cache, songs, *, execute, max_adds) -> {added, deferred, not_found:[{name,artist,key}]}`.

- [ ] Normalize both sides via `base._normalize`. Build `present_keys` from dest via `spotify_track_keys`.
- [ ] For each source track (oldest-first): skip if already present by key; else `dest.resolve(norm, cache)` → append id or record `not_found` (with its `track_key`). Cap at `max_adds` (rest `deferred`).
- [ ] `if execute: dest.add(dest_pl, ids)`.
- [ ] Test with fake source+dest providers: a matchable track is added; an unresolvable one lands in `not_found`; already-present is skipped.

## Task P3-3: TransferService + SyncService serialization

**Files:** `omni_sync/transfers.py` (TransferService); modify `sync_service.py`; `tests/test_transfers.py`.
**Produces:** `SyncService.run_exclusive(fn)` (awaits a shared lock — transfers queue, never coalesce-drop). `TransferService(settings, bus, sync)` with `submit(spec)->job_id`, `get(job_id)`, `resolve(job_id, key, dest_id)`; jobs in-memory (`{id, status, source, dest, result, conflicts}`).

- [ ] SyncService: add `self._lock = asyncio.Lock()`; wrap the pass in `run_now` with `async with self._lock`; add `run_exclusive(fn)` = `async with self._lock: return await asyncio.to_thread(fn)`. Existing coalesce test stays green.
- [ ] TransferService.submit: build source/dest via `build_one`, resolve/create dest playlist (reuse a factored `_ensure_target_playlist` helper), then `sync.run_exclusive(lambda: transfer(...))`; store result + conflicts; emit lifecycle events (`section`/`summary`) tagged `transfer`.
- [ ] resolve(job_id, key, dest_id): write `cache["search"][key]=dest_id` for the dest provider's cache file + persist; mark the conflict resolved.
- [ ] Test: submit runs the transfer under the lock; a transfer can't overlap a sync; resolve writes the cache.

## Task P3-4: Web routers

**Files:** `omni_sync/web/routers/transfers.py`; wire in `web/__init__.py`; `tests/test_web.py`.
**Produces:** `POST /api/transfers {source, source_playlist, dest, dest_playlist|dest_name}` → `{job_id}`; `GET /api/transfers/{id}` → job + conflicts; `POST /api/transfers/{id}/resolve {key, dest_id}`.

- [ ] App factory constructs `TransferService` (needs settings, bus, sync) → `app.state.transfers`.
- [ ] Router calls it; `POST` returns 202 + job_id. Test: submit (mocked transfer) returns a job; status reflects it.

## Task P3-5: Frontend — Transfers page + conflict review [delegate to subagent]

- [ ] Transfers page (route `/transfers`): pick source service + playlist and dest service + (playlist | "create new"), a "copy" button, live progress via the existing SSE feed (scoped by `transfer` tag), and a conflict list showing unmatched tracks with a resolve action. Reuse the design system; responsive; build/tsc/lint green.

## Task P3-6: E2E + docs

- [ ] Build SPA; against the live server verify `POST /api/transfers` (mock/without creds degrades gracefully), job status, and the `/transfers` route serves. README: "Transfer a playlist" note. Full `uv run pytest` green. Commit.

## Self-review
- §3.6 transfer→P3-2/3; §3.7 conflicts→P3-3 resolve; accessors (hook #7)→P3-1; API→P3-4; UI→P3-5.
- Isolation gate: `mirror_pair`/`reconcile` untouched; engine suite green.
- Deferred: mirror-mode transfers (with removals); search-assisted conflict picker (this phase resolves by dest id).
