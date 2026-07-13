# Design Brief — Omni Playlist Sync (Web UI Visual System)

You are designing the complete visual system and every screen for **Omni Playlist Sync**, a self-hosted web app. Deliver a cohesive, modern, production-ready design — design tokens plus screen-by-screen layouts — that a React/Tailwind team can implement directly. Do not redesign the information architecture or invent new features; make the existing product look and feel excellent, calm, and trustworthy.

## 1. Product context

Omni Playlist Sync is a **self-hosted, Soundiiz-style playlist tool** that mirrors, syncs, and transfers playlists across **Spotify, Apple Music, YouTube Music, and Jellyfin**. People run it on their own machine or home server and open it on their LAN from a laptop or phone.

- **Audience:** non-technical self-hosters. They are not developers; they want to connect their music accounts, press a button, and trust that their playlists stay in sync.
- **Emotional target:** *calm confidence.* This tool touches people's music libraries and their service credentials — it must feel safe, legible, and unhurried, never flashy or risky.
- **Tone:** trustworthy, quietly premium, music-adjacent (rhythm, waveform, spectrum motifs are welcome as restrained accents) but **never gimmicky** — no neon "DJ" clichés, no skeuomorphic vinyl, no aggressive gradients. Think a well-made utility (Linear/Plex/1Password calm) with a musical soul.
- **Multi-service identity matters:** the four services each have a recognizable brand color (Spotify green, Apple Music pink/red, YouTube red, Jellyfin purple/blue). The UI must let these coexist without clashing — reserve *service* colors for small identity accents (badges, dots) and keep the *app's own* accent distinct from all four so it never reads as "a Spotify app."

## 2. Screens to design

Design each at **desktop (≥1024px) and mobile (360px)**. All screens share a persistent app shell: top nav with the app mark, primary nav (Dashboard · Accounts · Playlists · Transfers · Settings), and a light/dark theme toggle. On mobile the nav collapses to a hamburger drawer.

1. **Dashboard** — the home. Contains:
   - A **sync status card**: current state (idle / running), next scheduled run, and a **last-pass summary** with per-service result chips showing `+added / −removed / ~held / ×missing` counts.
   - **Primary actions:** "Run now (dry-run)" and "Run now (execute)" (execute is the higher-commitment action — confirm dialog), plus pause/resume the schedule.
   - A **live sync feed**: a real-time stream of rows as a pass runs, each row typed by kind and color-coded — **add (green), remove (red), hold (amber), miss (muted/grey), warn (attention), section (a bold divider row), summary (emphasis)** — each tagged with the service it belongs to. Include **running counters** that tick up during a pass. Design its idle/empty state and its actively-streaming state.

2. **Accounts** — connect the four services. One **card per service** with the service identity and a **status pill**: `connected` (positive), `expired` (warning), `unconfigured` (neutral), `error` (danger). Each card has a Connect / Reconnect / Disconnect action. Connecting opens a **wizard modal that adapts to the auth type**:
   - **OAuth redirect (Spotify):** app credential fields → a "Connect" button that hands off to the provider; show the redirect URI to whitelist.
   - **OAuth device-code (YouTube):** show a **large, legible pairing code** + a verification URL/button + a "waiting for authorization…" polling state that resolves to success.
   - **Token paste (Apple):** secret text fields with helper text explaining where to get the tokens.
   - **API key (Jellyfin):** server URL + API key + optional user id.
   Design the field, secret-field, help-text, validating, success, and error states of this modal.

3. **Playlists** — two regions:
   - **Browse:** each connected service's playlists, grouped by provider, each row/tile with the playlist name and track count. Include not-connected, loading, empty, and error states per provider.
   - **Pairing editor** (modal): create an explicit link across services — a link name, a per-service selector (pick an existing playlist / "create by name" / not included), a **direction** control (one-way vs N-way), and an **enable** toggle. Also design the list of existing pairings (with direction + paused badges, edit, delete).

4. **Transfers** — a one-off "copy a playlist A→B" flow:
   - **Set up:** pick a **source** service + playlist and a **destination** service + (existing playlist **or** "create new" with a name), then a "Copy playlist" action (confirm).
   - **Run + watch:** live progress reusing the feed treatment.
   - **Conflict review:** a list of tracks that couldn't be matched on the destination (`name`, `artist`), each with a **resolve** action (a small form to supply the correct destination track). Design the resolved vs unresolved states.

5. **Settings** — sync mode (one-way / N-way, as a segmented/radio-card choice), schedule interval, add/removal caps (numeric), a playlist filter, and download-mirror options (format, path). Group related fields; make it scannable.

## 3. Design direction — what to deliver

- **Theme + brand accent:** choose ONE distinctive-but-tasteful app accent (not any of the four service brand colors) that carries buttons, links, focus, active nav, and key emphasis. Give **2–3 alternative accent directions** up front (e.g. a deep electric indigo, a warm amber/coral, a teal/aqua) with a one-line rationale each, then pick a default and build the system on it.
- **Full light AND dark palettes** — this app is theme-aware and both must be first-class (not a dark afterthought). Provide surface/background layers, text hierarchy (primary/secondary/muted), borders, accent + accent-hover/active, and semantic colors for the feed and status pills: success/add, danger/remove, warning/hold, neutral/miss, info. Verify contrast in both themes.
- **Service identity tokens:** a small swatch per service (spotify/apple/ytmusic/jellyfin) for badges and dots, tuned to sit calmly on both light and dark surfaces.
- **Typography:** a type scale (display → caption) and a font pairing (a characterful-but-legible UI face; a mono/tabular face for counts, codes, and the live feed). Specify weights and where tabular numerals are used (counters, code, counts).
- **Tokens:** spacing scale, corner radii, and an **elevation/shadow** system that reads well in both themes (dark mode leans on layered surfaces + subtle borders more than shadows).
- **Component styling** — give specs (default/hover/active/focus/disabled, sizes) for: buttons (primary/secondary/ghost/danger), cards, **status pills/badges**, inputs & selects, **toggles**, **radio-cards/segmented controls**, **modals & mobile bottom-sheets**, top nav + mobile drawer, list rows & the live-feed row, empty/loading(skeleton)/error states, and toasts.
- **Iconography:** specify one coherent line-icon approach (weight, corner style) and where icons appear; keep it minimal.
- **Motion:** subtle, purposeful transitions (nav, modal open, feed rows appending, status changes) with durations/easings; honor `prefers-reduced-motion`.
- **Responsive strategy:** mobile-first; collapsing nav; **≥44px tap targets**; inputs at ≥16px to avoid iOS zoom; multi-column grids collapse to one column; **no horizontal overflow down to 320px**; wide content (feed, counts, long track/token strings) wraps or scrolls within its own container.
- **Accessibility:** AA contrast in both themes, always-visible focus rings, status conveyed by more than color (icon/label + color), reduced-motion support.

## 4. Tech constraints — design WITH this stack, not around it

The app already exists in **React + Vite + TypeScript + Tailwind CSS v4**. Your design must be implementable directly on it:

- Tailwind v4 theme tokens live in `src/index.css` under an `@theme` block; dark mode is class-based (`prefers-color-scheme` default + a manual toggle). **Express your palette, type scale, spacing, radii, shadows, and motion as concrete `@theme` tokens / CSS custom properties** (name them, give light + dark values).
- A component library already exists and should be **refined, not replaced**: `Button`, `Card`, `StatusPill`, `Modal`, `TextField`, `SelectField`, `Toggle`, `RadioCard`, `ConfirmDialog`, `EmptyState`, `Skeleton`, `NavBar`. Map your specs onto these (variants, sizes, states) and note any new small primitive only if genuinely required.
- **No external/CDN assets at runtime** — the app runs offline on a LAN. Any fonts or icons must be self-hostable/bundled; do not rely on Google Fonts links or icon CDNs. If you choose custom fonts, name self-hostable options (and a system-font fallback stack).
- Prefer specs expressible as **Tailwind theme tokens + component classnames and annotated mockups**, not a Figma-only handoff.

## 5. What to hand back

1. **Accent options:** 2–3 candidate brand directions (swatches + one-line rationale), then your recommended default.
2. **Design-tokens block:** full light + dark palettes (incl. semantic + per-service tokens), type scale + font stack, spacing, radii, shadows, and motion — written as `@theme`-ready tokens/CSS variables.
3. **Component specs:** each component above with its variants, sizes, and interaction states.
4. **Screen-by-screen layouts:** Dashboard, Accounts (+ each wizard-modal auth variant), Playlists (browse + pairing editor), Transfers (setup + progress + conflict review), Settings — each at **desktop and mobile**, with key states (empty/loading/error, and the live-feed idle vs streaming) called out.
5. A short **implementation note** mapping your tokens/specs onto the existing files (`src/index.css` `@theme`, the listed components).

Keep it cohesive: one system, applied consistently, that makes a self-hoster feel their music is in careful hands.
