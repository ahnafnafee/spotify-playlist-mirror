# Web GUI — Phase 1 (Platform + Control Panel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the existing sync engine in a FastAPI web app that owns the schedule, connects accounts through the browser (OAuth where feasible), and streams a live view of each sync pass.

**Architecture:** Three layers, one-way deps: `web → services → engine`. The engine (`targets/`, `runner`, `matching`, `archive`) is touched only via four additive, default-preserving hooks (§8 of the design). New platform services (`settings`, `events`, `sync_service`, `accounts/`) are headless-testable; `web/` is thin FastAPI + a no-build HTMX/Alpine/Tailwind frontend.

**Tech Stack:** Python 3.13, FastAPI, Uvicorn, Jinja2, Pydantic (via FastAPI); spotipy (OAuth), ytmusicapi (device OAuth); HTMX + Alpine.js (vendored), Tailwind Play CDN; pytest.

**Design doc:** `docs/design/2026-07-12-web-gui-architecture.md` (read §3, §8 before implementing).

## Global Constraints

- Python `>=3.13`. New runtime deps added to `pyproject.toml` core: `fastapi`, `uvicorn[standard]`, `jinja2`.
- **Layer rule:** nothing in the engine imports `web/` or `accounts/`. The `Event` dataclass lives in `logs.py` (a leaf) so `events.py` importing it stays services→engine.
- **Every engine hook defaults to today's behavior.** The existing Spotify→Apple/YTMusic path must stay byte-for-byte unchanged unless a new path opts in. `test_matching.py` + `test_reconcile.py` must stay green.
- **Env is the engine's config channel.** SettingsStore owns a managed env file (`data/app.env`); the engine loads that. Never let a stale root `.env` clobber a wizard-saved credential.
- **One serialization point.** All engine invocations (scheduled syncs + future transfers) go through SyncService's single queue — never two passes at once (shared on-disk caches + SQLite are not concurrent-safe).
- LAN bind, no auth (accepted gap, design §7).
- Tests are `test_*.py` at repo root (matches existing convention). Commits: Conventional Commits, no AI-attribution trailer.
- Run tests with the project venv: `uv run pytest <file> -v`.

---

## File structure

**Engine hooks (modify):**
- `omni_sync/logs.py` — add `Event` dataclass + `set_sink`; each `log_*` emits an Event.
- `omni_sync/runner.py` — `run_pass` returns `PassSummary`; `_run_nway` accumulates stats.

**New services (create):**
- `omni_sync/settings.py` — `SettingsStore`: `settings.json` ⇄ `data/app.env` ⇄ `os.environ`.
- `omni_sync/events.py` — `EventBus`: loop-safe pub/sub fed by the logs sink.
- `omni_sync/sync_service.py` — `SyncService`: single queue, scheduler, `run_now`, lifecycle events.
- `omni_sync/accounts/__init__.py` — `CONNECTORS` registry.
- `omni_sync/accounts/base.py` — `Connector` protocol, `Field`, `ConnStatus`, `DeviceCode`.
- `omni_sync/accounts/spotify.py` `ytmusic.py` `apple.py` `jellyfin.py` — one connector each.

**New web layer (create):**
- `omni_sync/web/__init__.py` — app factory: mount routers/static, wire sink, start scheduler.
- `omni_sync/web/routers/{accounts,settings,sync,events,pages}.py`
- `omni_sync/web/templates/{base,dashboard,accounts,settings}.html`
- `omni_sync/web/static/{app.css,app.js,vendor/htmx.min.js,vendor/alpine.min.js}`

**Deploy (modify):** `Dockerfile`, `docker-compose.yml`.

**Tests (create):** `test_logs_sink.py`, `test_settings.py`, `test_events.py`, `test_sync_service.py`, `test_connectors.py`, `test_web.py`.

---

## Task 0: Feature branch, deps, package skeleton

**Files:** Modify `pyproject.toml`; Create `omni_sync/web/__init__.py`, `omni_sync/web/routers/__init__.py`, `test_web.py`.

- [ ] **Step 1:** Branch off main: `git checkout main && git pull && git checkout -b feat/web-gui`.
- [ ] **Step 2:** Add deps to `pyproject.toml` `dependencies`: `"fastapi>=0.115"`, `"uvicorn[standard]>=0.32"`, `"jinja2>=3.1"`. Run `uv sync`.
- [ ] **Step 3: Write failing test** `test_web.py`:

```python
from fastapi.testclient import TestClient
from omni_sync.web import create_app

def test_health():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}
```

- [ ] **Step 4:** Run `uv run pytest test_web.py -v` → FAIL (no `create_app`).
- [ ] **Step 5:** Minimal `web/__init__.py`:

```python
from fastapi import FastAPI

def create_app() -> FastAPI:
    app = FastAPI(title="Omni Playlist Sync")
    @app.get("/health")
    def health(): return {"ok": True}
    return app

app = create_app()
```

- [ ] **Step 6:** Run `uv run pytest test_web.py -v` → PASS.
- [ ] **Step 7:** Commit: `feat(web): scaffold FastAPI app with health check`.

---

## Task 1: Event type + logs sink (engine hook #1)

**Files:** Modify `omni_sync/logs.py`; Create `test_logs_sink.py`.
**Interfaces — Produces:** `logs.Event(ts, kind, tag, message, data=None)`; `logs.set_sink(fn)`; `log_*` emit an Event whose `kind` ∈ {add,remove,hold,miss,note,warn,summary,section}.

- [ ] **Step 1: Write failing test** `test_logs_sink.py`:

```python
from omni_sync import logs

def test_sink_receives_typed_events():
    seen = []
    logs.set_sink(seen.append)
    try:
        logs.log_add("X - Y", tag="apple")
        logs.log_remove("A - B", tag="yt")
    finally:
        logs.set_sink(None)
    kinds = [(e.kind, e.tag) for e in seen]
    assert ("add", "apple") in kinds and ("remove", "yt") in kinds

def test_sink_none_is_noop():
    logs.set_sink(None)
    logs.log_note("hi")  # must not raise
```

- [ ] **Step 2:** Run → FAIL (`Event`/`set_sink` missing).
- [ ] **Step 3: Implement.** In `logs.py` add (near top, after imports):

```python
import time as _time
from dataclasses import dataclass

@dataclass(frozen=True)
class Event:
    ts: float; kind: str; tag: str; message: str; data: dict | None = None

_sink = None
def set_sink(fn): 
    global _sink
    _sink = fn

def _emit(kind, message, tag):
    fn = _sink
    if fn is not None:
        try: fn(Event(_time.time(), kind, tag or "", message, None))
        except Exception: pass   # a broken sink must never break a sync
```

Then in each `log_*` helper, after its `log(...)` call, add `_emit("<kind>", msg, tag)`. Map: `log_add`→"add", `log_remove`→"remove", `log_hold`→"hold", `log_miss`→"miss", `log_warn`→"warn", `log_note`→"note", `log_summary`→"summary". In `log_section`, emit `_emit("section", title, tag)`.

- [ ] **Step 4:** Run `uv run pytest test_logs_sink.py -v` → PASS.
- [ ] **Step 5:** Regression: `uv run pytest test_matching.py test_reconcile.py -v` → PASS (engine untouched behaviorally).
- [ ] **Step 6:** Commit: `feat(logs): optional structured event sink for live view`.

---

## Task 2: EventBus with thread→loop bridge (engine hook #2)

**Files:** Create `omni_sync/events.py`; Modify `test_events.py`.
**Interfaces — Consumes:** `logs.Event`, `logs.set_sink`. **Produces:** `EventBus.subscribe()->asyncio.Queue`, `.unsubscribe(q)`, `.publish(Event)` (thread-safe), `.recent()->list[Event]`, `.attach_to_logs()` (registers a sink that publishes).

- [ ] **Step 1: Write failing test** `test_events.py` (publish from a worker thread, async subscriber receives):

```python
import asyncio, threading
from omni_sync.logs import Event
from omni_sync.events import EventBus

def test_publish_from_worker_thread_reaches_subscriber():
    async def scenario():
        bus = EventBus(); bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        threading.Thread(target=lambda: bus.publish(Event(0.0,"add","apple","x"))).start()
        e = await asyncio.wait_for(q.get(), 2.0)
        assert e.kind == "add"
    asyncio.run(scenario())
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `events.py`:

```python
import asyncio
from collections import deque
from .logs import Event, set_sink

class EventBus:
    def __init__(self, ring: int = 500):
        self._loop = None
        self._subs: set[asyncio.Queue] = set()
        self._ring: deque[Event] = deque(maxlen=ring)

    def bind_loop(self, loop): self._loop = loop
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(); self._subs.add(q); return q
    def unsubscribe(self, q): self._subs.discard(q)
    def recent(self): return list(self._ring)

    def publish(self, e: Event):
        # Called from arbitrary worker threads; asyncio.Queue is not thread-safe.
        if self._loop is None: return
        self._loop.call_soon_threadsafe(self._deliver, e)

    def _deliver(self, e: Event):
        self._ring.append(e)
        for q in self._subs:
            try: q.put_nowait(e)
            except asyncio.QueueFull: pass

    def attach_to_logs(self): set_sink(self.publish)
```

- [ ] **Step 4:** Run `uv run pytest test_events.py -v` → PASS.
- [ ] **Step 5:** Commit: `feat(events): loop-safe EventBus bridging worker threads to SSE`.

---

## Task 3: run_pass returns PassSummary (engine hook #3)

**Files:** Modify `omni_sync/runner.py`.
**Interfaces — Produces:** `run_pass(opts) -> dict` with keys `{mode, execute, duration_s, ok, error, per_target: [{name, added, removed, missing, held, deferred, created, skipped}]}`. CLI ignores the return (unchanged).

- [ ] **Step 1: Write failing test** in `test_web.py` (or new `test_runner_summary.py`) — assert the one-way path returns per-target aggregates. Use a monkeypatched `build_targets` returning a fake target and a fake `spotify.client`/`playlists_by_name` so no network is hit. (At execution: build a minimal fake `MirrorTarget` with empty playlists so a pass runs to completion instantly.)
- [ ] **Step 2:** Run → FAIL (`run_pass` returns `None`).
- [ ] **Step 3: Implement.** In `runner.py`:
  - Build a `summary` dict at the top of `run_pass` (`mode=opts.sync_mode`, `execute=opts.execute`, `per_target=[]`, `ok=True`, `error=None`).
  - One-way path: after the loop, populate `per_target` from the existing `results` aggregates (already computed at lines ~201-212).
  - N-way path (`_run_nway`): accumulate each `reconcile(...)` return (currently discarded ~line 280) into a per-run aggregate; return it up so `run_pass` can fold it into `per_target` (single synthetic "N-way" row keyed by playlist counts, or per-peer — implementer's call; keep the same key names).
  - `refresh_local` early return: `summary` with empty `per_target`.
  - `return summary` on every path. Wrap the body so an exception sets `ok=False, error=repr(e)` and still returns (the loop in `cli.main` already handles exceptions separately, so keep `raise` semantics there — return summary only on the success paths; SyncService catches exceptions itself).
- [ ] **Step 4:** Run the new test → PASS; `uv run pytest test_reconcile.py -v` → PASS.
- [ ] **Step 5:** Commit: `feat(runner): run_pass returns a per-pass summary`.

---

## Task 4: SettingsStore (engine hook #4)

**Files:** Create `omni_sync/settings.py`, `test_settings.py`.
**Interfaces — Produces:** `SettingsStore(dir="data")` with `.load()->dict`, `.save(dict)`, `.get(key)`, `.env_path` (`data/app.env`), `.apply_to_env()`. `save()` writes `settings.json` AND regenerates `app.env` AND updates `os.environ`.

- [ ] **Step 1: Write failing test** `test_settings.py` — the clobber regression:

```python
import os
from dotenv import load_dotenv
from omni_sync.settings import SettingsStore

def test_saved_credential_survives_dotenv_reload(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"APPLE_BEARER_TOKEN": "NEW"})
    # simulate a stale hand-edited file existing elsewhere; engine loads the MANAGED file
    load_dotenv(store.env_path, override=True)
    assert os.environ["APPLE_BEARER_TOKEN"] == "NEW"

def test_roundtrip(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"SYNC_INTERVAL": "30m", "SPOTIFY_CLIENT_ID": "abc"})
    assert store.load()["SYNC_INTERVAL"] == "30m"
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `settings.py`: JSON persistence to `<dir>/settings.json`; `apply_to_env()` sets `os.environ[k]=str(v)` for each; render `<dir>/app.env` as `KEY=value` lines with values quoted via `shlex.quote`. `save()` = write json → render env file → apply_to_env. Only string-able scalars; skip `None`.
- [ ] **Step 4:** Run `uv run pytest test_settings.py -v` → PASS.
- [ ] **Step 5:** Commit: `feat(settings): managed settings store with env projection`.

---

## Task 5: SyncService (single queue + scheduler)

**Files:** Create `omni_sync/sync_service.py`, `test_sync_service.py`.
**Interfaces — Consumes:** `runner.run_pass`, `config.Options`/`parse_args`, `settings.SettingsStore`, `events.EventBus`. **Produces:** `SyncService(settings, bus)` with async `start()`/`stop()`, `run_now(execute: bool) -> None` (coalesced), `status() -> dict`. Runs `run_pass` in a thread via `asyncio.to_thread`; publishes lifecycle Events (`kind="section"`/`"summary"` reused, or `kind="note"` with `data={"lifecycle": ...}`); enforces one pass at a time with an `asyncio.Lock` + a "busy" flag.

- [ ] **Step 1: Write failing test** `test_sync_service.py` — serialization + coalesce, with a fake `run_pass` that sleeps:

```python
import asyncio
from omni_sync.sync_service import SyncService

def test_run_now_serializes(monkeypatch, tmp_path):
    calls = []
    async def scenario():
        import omni_sync.sync_service as m
        async def fake_pass(opts):
            calls.append("start"); await asyncio.sleep(0.05); calls.append("end")
        monkeypatch.setattr(m, "_run_pass_async", fake_pass)
        from omni_sync.settings import SettingsStore
        from omni_sync.events import EventBus
        bus = EventBus(); bus.bind_loop(asyncio.get_running_loop())
        svc = SyncService(SettingsStore(dir=tmp_path), bus)
        await asyncio.gather(svc.run_now(False), svc.run_now(False))
    asyncio.run(scenario())
    # second overlapping call coalesced → not start,start,end,end
    assert calls == ["start", "end"]
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `sync_service.py`: an `asyncio.Lock`; `run_now` returns immediately if `locked()` (coalesce) else acquires, applies settings→env at the boundary, builds `Options` from settings, `await asyncio.to_thread(run_pass, opts)`, publishes start/finish lifecycle events with the returned summary. `start()` launches a scheduler task looping `sleep(interval); await self.run_now(execute=True)`. Provide the seam `_run_pass_async` the test patches (a thin `async def` wrapping `to_thread(run_pass, opts)`).
- [ ] **Step 4:** Run `uv run pytest test_sync_service.py -v` → PASS.
- [ ] **Step 5:** Commit: `feat(sync): single-queue scheduler and run-now service`.

---

## Task 6: Connector protocol + Jellyfin + Apple connectors

**Files:** Create `accounts/base.py`, `accounts/__init__.py`, `accounts/jellyfin.py`, `accounts/apple.py`, `test_connectors.py`.
**Interfaces — Produces:** `accounts.base.{Connector, Field, ConnStatus, DeviceCode}`; `accounts.CONNECTORS: dict[str, Connector]`. Each connector: `id`, `name`, `auth_kind`, `config_fields`, `status()->ConnStatus`, and the methods for its kind. Connectors take a `SettingsStore` so they read/write config uniformly.

- [ ] **Step 1: Write failing test** `test_connectors.py`:

```python
from omni_sync.settings import SettingsStore
from omni_sync.accounts import CONNECTORS

def test_apple_status_unconfigured(tmp_path):
    store = SettingsStore(dir=tmp_path)
    c = CONNECTORS["apple"](store)
    assert c.status().state == "unconfigured"

def test_apple_submit_stores_tokens(tmp_path, monkeypatch):
    store = SettingsStore(dir=tmp_path)
    c = CONNECTORS["apple"](store)
    monkeypatch.setattr(c, "_validate", lambda: True)  # skip network
    st = c.submit({"APPLE_BEARER_TOKEN": "b", "APPLE_USER_TOKEN": "u"})
    assert st.state == "connected" and store.get("APPLE_USER_TOKEN") == "u"
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.** `base.py`: the dataclasses + a `Protocol`. `jellyfin.py` (`api_key` kind: fields url/api_key/user_id; `status` = configured?; `submit` stores + optional ping). `apple.py` (`token_paste`: fields bearer/user token; `_validate()` GETs `AMP + f"/me/library/playlists?limit=1"` with `_headers`-style auth; `submit` stores then validates). `__init__.py`: `CONNECTORS = {"spotify": SpotifyConnector, "ytmusic": YTMusicConnector, "apple": AppleConnector, "jellyfin": JellyfinConnector}` (spotify/ytmusic added in Tasks 7–8; import lazily or add now as stubs).
- [ ] **Step 4:** Run `uv run pytest test_connectors.py -v` → PASS.
- [ ] **Step 5:** Commit: `feat(accounts): connector protocol + Apple/Jellyfin connectors`.

---

## Task 7: Spotify connector (OAuth redirect)

**Files:** Create `accounts/spotify.py`; extend `test_connectors.py`.
**Interfaces — Produces:** `SpotifyConnector(store)`: `config_fields`=[client_id, client_secret]; `begin_redirect(redirect_uri)->str` (auth URL); `complete_redirect(params)->ConnStatus` (exchange code, write token cache); `status()` = token cache exists & config present.

- [ ] **Step 1: Write failing test** — `begin_redirect` returns a URL containing the client_id and redirect_uri; use a fake `SpotifyOAuth` (monkeypatch) so no network.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** using `spotipy.oauth2.SpotifyOAuth` (see `spotify.py:44-62` for the exact params): construct with `open_browser=False`, `cache_path=store.get("SPOTIFY_TOKEN_CACHE", "data/spotify_token_cache")`, scope `playlist-read-private` (+ modify scopes if N-way is enabled in settings). `begin_redirect` → `auth.get_authorize_url()`. `complete_redirect` → `code = auth.parse_response_code(params["url"])` then `auth.get_access_token(code, as_dict=False, check_cache=False)` (writes the cache). `status` → `connected` if the cache file exists and config is set, else `unconfigured`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit: `feat(accounts): Spotify OAuth-redirect connector`.

---

## Task 8: YouTube Music connector (OAuth device)

**Files:** Create `accounts/ytmusic.py`; extend `test_connectors.py`.
**Interfaces — Produces:** `YTMusicConnector(store)`: config_fields=[client_id, client_secret]; `begin_device()->DeviceCode(user_code, verification_url, device_code, interval)`; `poll_device(dc)->ConnStatus` (writes `ytmusic_oauth.json`); `status()` = oauth json exists & config present.

- [ ] **Step 1: VERIFY the installed ytmusicapi device-OAuth API first** (version in `.venv`): `uv run python -c "from ytmusicapi.auth.oauth import OAuthCredentials; help(OAuthCredentials)"`. Confirm the code/token method names (`get_code`, `token_from_code` or equivalent) before writing the connector. (`ytmusic.py:53-58` already uses `OAuthCredentials(client_id=, client_secret=)`.)
- [ ] **Step 2: Write failing test** with a fake `OAuthCredentials` (monkeypatch) returning a canned code + token; assert `begin_device` surfaces the user_code and `poll_device` writes the json.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4: Implement** wrapping the verified API; write the refresh token to `store.get("YTMUSIC_AUTH_FILE", "data/ytmusic_oauth.json")`.
- [ ] **Step 5:** Run → PASS.
- [ ] **Step 6:** Commit: `feat(accounts): YouTube Music OAuth-device connector`.

---

## Task 9: Web routers (accounts, settings, sync, events/SSE)

**Files:** Create `web/routers/{accounts,settings,sync,events,pages}.py`; extend `test_web.py`.
**Interfaces — Consumes:** all services; app state holds `settings`, `bus`, `sync` (set by the app factory). **Produces:** the endpoints in design §4.

- [ ] **Step 1: Write failing tests** in `test_web.py`: `GET /api/accounts` → list with a `state` each; `PUT /api/settings` round-trips; `POST /api/sync/run?execute=0` → 202 and `status()` shows a run; `GET /events` → `text/event-stream` header (read one chunk).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** routers. `events.py` returns `StreamingResponse(gen(), media_type="text/event-stream")` where `gen` subscribes a queue from `app.state.bus`, first replays `bus.recent()`, then `yield f"data: {json.dumps(asdict(e))}\n\n"` per event; unsubscribe on disconnect. `sync.py` calls `app.state.sync.run_now(...)`. `accounts.py` maps to `CONNECTORS`. `pages.py` renders Jinja templates.
- [ ] **Step 4:** Run `uv run pytest test_web.py -v` → PASS.
- [ ] **Step 5:** Commit: `feat(web): accounts, settings, sync, and SSE routers`.

---

## Task 10: Frontend (dashboard, wizard, live view, settings)

**Files:** Create `web/templates/*.html`, `web/static/app.css`, `web/static/app.js`, vendored `web/static/vendor/{htmx.min.js,alpine.min.js}`.
**Verification is visual + smoke** (no browser test framework): TestClient asserts pages return 200 and contain anchor strings; manual check via `uv run uvicorn omni_sync.web:app` in the /run step.

- [ ] **Step 1:** Vendor htmx + alpine into `static/vendor/` (download the minified files; they're MIT and small). Tailwind via Play CDN `<script src="https://cdn.tailwindcss.com">` in `base.html` (LAN clients still fetch it; acceptable — or vendor a prebuilt CSS if fully-offline is required).
- [ ] **Step 2:** `base.html` — layout shell (nav: Dashboard / Accounts / Settings), theme-aware, includes vendored JS + app.css/js.
- [ ] **Step 3:** `dashboard.html` — status card (running / next run / last summary), "Run now (dry-run)" + "Run now (execute)" buttons (HTMX POST), and the **Live view**: an Alpine component that opens `EventSource("/events")` and appends colored rows (add=green, remove=red, hold=amber, miss=grey, warn=bold) with running counters.
- [ ] **Step 4:** `accounts.html` — one card per connector with a status pill; a wizard modal per `auth_kind` (redirect button / device-code panel with poll / paste form / api-key form) driven by `config_fields`.
- [ ] **Step 5:** `settings.html` — sync mode, interval, caps, playlist picker (checkbox list from a `GET /api/playlists?provider=spotify`-lite: reuse `spotify.playlists_by_name` names), download/Jellyfin options.
- [ ] **Step 6:** TestClient smoke: `GET /`, `/accounts`, `/settings` → 200 with anchor strings. Commit: `feat(web): dashboard, accounts wizard, live view, settings UI`.

---

## Task 11: App factory wiring + Docker + end-to-end run

**Files:** Modify `web/__init__.py`, `Dockerfile`, `docker-compose.yml`.

- [ ] **Step 1:** App factory: on startup, construct `SettingsStore`, `EventBus` (`bind_loop(asyncio.get_running_loop())`, `attach_to_logs()`), `SyncService`; store on `app.state`; `await sync.start()`. On shutdown, `await sync.stop()`. Mount routers + `StaticFiles`. Point the engine's dotenv at `data/app.env` (set `SPOTIFY_TOKEN_CACHE`/`*_FILE` defaults under `data/`).
- [ ] **Step 2:** `Dockerfile` CMD → `["uvicorn", "omni_sync.web:app", "--host", "0.0.0.0", "--port", "8080"]`. `docker-compose.yml`: expose `8080:8080`, keep `./data` + music volumes, drop the bare-loop assumption (SyncService schedules).
- [ ] **Step 3: Manual end-to-end** (use the `/run` skill or): `uv run uvicorn omni_sync.web:app --port 8080`, open `http://127.0.0.1:8080`, verify: dashboard loads, connect a service via the wizard, click Run now (dry-run), watch the live feed populate. Fix anything that only shows up live.
- [ ] **Step 4:** Full regression: `uv run pytest -v` (all green). Commit: `feat(web): wire app factory, scheduler startup, and Docker entrypoint`.
- [ ] **Step 5:** Update `README.md` with a "Web GUI" quickstart section. Commit: `docs: document the web GUI quickstart`.

---

## Self-review

- **Spec coverage:** §3.3 SettingsStore→T4; §3.4 EventBus+sink→T1,T2; §3.5 SyncService→T5 (+T3 summary); §3.2 connectors→T6-8; §4 API→T9; §6 frontend→T10; §7 security = LAN bind in T11; §8 hooks #1-#4 = T1,T2,T3,T4. Phase-2/3 hooks (#5-#10) intentionally deferred to their own plans.
- **Placeholder scan:** T8/T10 carry an explicit VERIFY step (ytmusicapi API, vendored asset URLs) rather than guessed code — deliberate, not a placeholder. No "TBD/handle edge cases".
- **Type consistency:** `PassSummary` keys (T3) reused by SyncService status (T5) and dashboard (T10); `ConnStatus.state` values reused across T6-9; `Event` fields (T1) consumed by T2/T9.

## Deferred to later plans
- **Phase 2** (playlist browser + `PlaylistLink` pairing, engine hooks #9, #5, start #10): `docs/plans/…-web-gui-phase2.md`.
- **Phase 3** (transfers + conflict resolution, engine hooks #5-#8, #10): `docs/plans/…-web-gui-phase3.md`.
