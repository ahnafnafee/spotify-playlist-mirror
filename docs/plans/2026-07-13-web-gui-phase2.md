# Web GUI — Phase 2 (Playlist Browser + Explicit Pairing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Browse each connected service's playlists in the UI, and let users explicitly pair playlists across services (including differently-named ones) with a per-pair direction — overriding the implicit same-name matching.

**Architecture:** Additive on Phase 1. A new `PlaylistService` + `PlaylistLink` store sit in the services tier; the engine gains a `link_key` seam so a pair can share a canonical state key independent of display name, and `run_pass` consults explicit links before falling back to name-match. Frontend adds a Playlists page.

**Design doc:** `docs/design/2026-07-12-web-gui-architecture.md` §3.8, §8 (hooks #5, #9), §9 Phase 2.

## Global Constraints

- Same as Phase 1: engine edits are additive + default-preserving (existing name-match behavior unchanged when no links exist); layer rule `web → services → engine`; tests `test_*.py` at repo root; `uv run pytest`; Conventional Commits, no AI trailer.
- **Backward compatibility is the gate:** with zero `PlaylistLink`s configured, a pass must behave exactly as today. `test_reconcile.py` + `test_runner_summary.py` stay green.
- Spotify remains the source anchor in Phase 2 (making Spotify fully optional is Phase 3, hook #10). Pairing maps Spotify playlists to differently-named target playlists.

---

## File structure

- **Create** `omni_sync/playlists.py` — `PlaylistService` (browse) + `PlaylistLink` dataclass + `LinkStore` (`data/links.json`).
- **Create** `omni_sync/web/routers/playlists.py` — `/api/playlists`, `/api/links` CRUD.
- **Modify** `omni_sync/targets/__init__.py` — `build_one(provider_id, opts, sp=None)` (hook #5).
- **Modify** `omni_sync/targets/base.py` — `reconcile(..., link_key=None)` and `mirror_pair(..., link_key=None)` (hook #9).
- **Modify** `omni_sync/runner.py` — consult `LinkStore`: an explicit link resolves members + reconciles under its `link_key`; no links → today's name-match path.
- **Modify** `omni_sync/web/__init__.py` — include the playlists router.
- **Frontend** `frontend/src` — Playlists page (browse + pair) — delegated to a subagent.
- **Tests** `test_playlists.py`; extend `test_web.py`.

---

## Task 1: build_one registry function (hook #5)

**Files:** Modify `targets/__init__.py`; Create `test_playlists.py`.
**Interfaces — Produces:** `targets.build_one(provider_id: str, opts, sp=None) -> MirrorTarget | None` — constructs exactly one provider from the registry (None if unconfigured).

- [ ] **Step 1: Failing test** `test_playlists.py`:

```python
from omni_sync import targets
from omni_sync.config import parse_args

def test_build_one_unknown_returns_none():
    assert targets.build_one("nope", parse_args([])) is None
```

- [ ] **Step 2:** Run → FAIL (no `build_one`).
- [ ] **Step 3: Implement** in `targets/__init__.py`:

```python
def build_one(provider_id, opts, sp=None):
    """Construct a single provider by id (None if unknown/unconfigured)."""
    builder = _REGISTRY.get(provider_id)
    return builder(opts, sp) if builder else None
```

Add `"build_one"` to `__all__`.

- [ ] **Step 4:** Run → PASS. **Step 5:** Commit `feat(targets): build_one to construct a single provider by id`.

---

## Task 2: PlaylistService.browse + /api/playlists

**Files:** Create/extend `omni_sync/playlists.py`, `omni_sync/web/routers/playlists.py`; extend `test_web.py`.
**Interfaces — Produces:** `PlaylistService(settings).browse(provider_id) -> list[dict]` each `{id, name, count}`; `GET /api/playlists?provider=<id>`.

- [ ] **Step 1: Failing test** in `test_playlists.py` — monkeypatch `build_one` to a fake target whose `list_playlists()` returns `{ "chill": {"id":"1","name":"Chill"} }`; assert `browse("spotify")` returns `[{"id":"1","name":"Chill","count":...}]`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `PlaylistService.browse`: `apply_to_env()`, build opts via `parse_args([])`, `t = build_one(provider_id, opts, sp=<spotify client if provider needs it>)`; return normalized rows from `t.list_playlists()` values (use `playlist_count` when available). For the spotify provider, construct the client via `spotify.client()`; guard missing creds → return `[]` with a note. Router `GET /api/playlists` calls it.
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit `feat(web): browse a provider's playlists`.

---

## Task 3: PlaylistLink model + LinkStore + /api/links

**Files:** extend `omni_sync/playlists.py`, `web/routers/playlists.py`; extend `test_playlists.py`.
**Interfaces — Produces:**

```python
@dataclass
class PlaylistLink:
    id: str; name: str
    members: dict[str, str | None]   # provider_id -> playlist_id (None = "create by name")
    direction: Literal["oneway", "nway"] = "oneway"
    source: str | None = "spotify"
    enabled: bool = True

class LinkStore:                     # data/links.json
    def list(self) -> list[PlaylistLink]: ...
    def upsert(self, link: PlaylistLink) -> PlaylistLink: ...   # generates id if missing
    def delete(self, link_id: str) -> None: ...
```

- [ ] **Step 1: Failing test** — `LinkStore(dir=tmp).upsert(...)` then `list()` round-trips; `delete` removes.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** JSON persistence (owner-only via the same `_open_private` pattern as SettingsStore — links reference playlist ids, not secrets, but keep `data/` consistent). Router: `GET /api/links`, `PUT /api/links` (upsert), `DELETE /api/links/{id}`.
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit `feat(web): playlist pairing links (CRUD)`.

---

## Task 4: reconcile / mirror_pair link_key (hook #9)

**Files:** Modify `targets/base.py`; extend `test_reconcile.py`.
**Interfaces — Produces:** `reconcile(peers, name, playlists, caches, songs, *, execute, max_removals, max_adds, link_key=None)` and `mirror_pair(..., link_key=None)`. When `link_key` is None the key stays `name.casefold()` (today). When given, it addresses the canonical/archive state, so two differently-named paired playlists share one logical identity.

- [ ] **Step 1: Failing test** in `test_reconcile.py` — call `reconcile(..., link_key="fixedkey")` for two peers whose playlists have DIFFERENT names; assert the persisted state is keyed by `"fixedkey"` (via `archive.get_playlist_state(songs, "fixedkey", src)`), not either display name.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** — `base.py:265` becomes `key = link_key or name.casefold()`. Same one-line pattern in `mirror_pair` for its archive state (`run_target` passes it through). No other logic changes.
- [ ] **Step 4:** Run → PASS; existing `test_reconcile.py` stays green (default path). **Step 5:** Commit `feat(engine): explicit link_key for cross-name playlist pairing`.

---

## Task 5: run_pass consults PlaylistLinks

**Files:** Modify `runner.py`, `run_target` (accept + pass `link_key`); extend `test_runner_summary.py`.
**Interfaces — Consumes:** `LinkStore`, `build_one`. **Behavior:** at pass start, load enabled links. For each link: resolve each member (explicit `playlist_id`, else name-match), then run the existing `mirror_pair`/`reconcile` with `link_key=link.id` and the link's direction. Playlists NOT covered by any link fall through to today's name-match path unchanged. Empty LinkStore → identical to today.

- [ ] **Step 1: Failing test** — with a fake LinkStore returning one link pairing a Spotify playlist to a differently-named target, assert the pass reconciles them under `link.id` (patch reconcile/mirror_pair to capture the `link_key` argument).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** the links-aware selection in `run_pass` (a helper `_links_for(opts)` reading `LinkStore`; a resolver `_resolve_members(link, dirs)`), threading `link_key`. Keep the change isolated behind "links exist" so the default path is untouched.
- [ ] **Step 4:** Run → PASS; full engine regression green. **Step 5:** Commit `feat(sync): honor explicit playlist pairings in a pass`.

---

## Task 6: Frontend — Playlists page (browse + pair) [delegate to subagent]

**Files:** `frontend/src` (new `pages/Playlists.tsx`, components, a `useLinks` hook, nav entry).
- Browse each connected provider's playlists (`GET /api/playlists?provider=`).
- Create/edit a pairing: pick a playlist per provider (or "create by name"), set direction, enable/disable — `PUT/DELETE /api/links`.
- Responsive + matches the existing design system. `pnpm build` + `tsc` + lint green.
- [ ] Delegate to the `frontend` subagent with the /api/playlists + /api/links contract; verify build.
- [ ] Commit `feat(web): playlists browser + pairing UI`.

---

## Task 7: E2E + docs

- [ ] Build SPA; launch uvicorn; verify `/api/playlists?provider=spotify` (with creds) and `/api/links` round-trip; a paired sync reconciles the linked pair.
- [ ] README: short "Pair playlists" note under the Web GUI section.
- [ ] Full `uv run pytest` green. Commit.

---

## Self-review
- Spec coverage: §3.8 browse→T2, pairing model→T3, link_key→T4, engine integration→T5; hook #5→T1; frontend→T6.
- Backward-compat gate: T4/T5 default paths keep `test_reconcile.py` green (empty links = today's behavior).
- Deferred to Phase 3: hook #10 (Spotify-optional), transfers, conflict resolution.
