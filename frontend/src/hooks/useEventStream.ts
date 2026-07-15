import { useCallback, useEffect, useState } from 'react'

import type { SyncEvent } from '../types'

// Mirrors the backend EventBus's own ring-buffer size (events.py) — plenty
// of history for a session without the DOM list growing unbounded.
const MAX_EVENTS = 500
const PASS_STARTED_RE = /pass started/i

// Recent feed history survives a reload via localStorage, capped
// independently (and much tighter) than the in-memory ring buffer above so
// persisted storage stays small regardless of how long a live session runs.
// "v1" so a future change to SyncEvent's shape can invalidate old entries by
// bumping the key rather than needing a migration.
const STORAGE_KEY = 'songmirror-live-feed-v1'
const PERSIST_MAX_EVENTS = 200

export interface EventCounters {
  added: number
  removed: number
  held: number
  missing: number
}

const ZERO_COUNTERS: EventCounters = { added: 0, removed: 0, held: 0, missing: 0 }

/** Reads the persisted feed history. Defensive on every axis — a disabled
 * or inaccessible store (SSR, privacy mode, a browser extension blocking
 * storage), a missing key, or corrupted JSON all just mean "no history to
 * restore" rather than a render crash. */
function loadPersistedEvents(): SyncEvent[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as SyncEvent[]) : []
  } catch {
    // Malformed JSON (or a stale schema under this key) — clear it so it
    // doesn't keep failing to parse on every future load.
    try {
      window.localStorage?.removeItem(STORAGE_KEY)
    } catch {
      // Storage inaccessible even for a clear — nothing more we can do.
    }
    return []
  }
}

/** Best-effort mirror of the in-memory feed to localStorage, capped to the
 * most recent PERSIST_MAX_EVENTS (oldest roll off first). Never throws —
 * e.g. Safari private browsing throws on `setItem` once its (tiny) private
 * quota fills, and that should just mean "this reload won't be restored",
 * not a crash. */
function persistEvents(events: SyncEvent[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    const capped = events.length > PERSIST_MAX_EVENTS ? events.slice(events.length - PERSIST_MAX_EVENTS) : events
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(capped))
  } catch {
    // Unavailable/full storage — history just won't survive this reload.
  }
}

/** Subscribes to the /events SSE stream for the lifetime of the component.
 * `EventSource` retries dropped connections on its own; we just track
 * connected/disconnected for the UI indicator.
 *
 * Recent history is persisted to localStorage (see STORAGE_KEY) so a reload
 * restores it instead of starting blank. The dashboard's live feed and the
 * Transfers page's live feed each call this hook independently, but since
 * the persistence logic lives here — once — rather than in either caller,
 * both read and write the same key and never diverge. */
export function useEventStream() {
  const [events, setEvents] = useState<SyncEvent[]>(loadPersistedEvents)
  const [counters, setCounters] = useState<EventCounters>(ZERO_COUNTERS)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const source = new EventSource('/events')

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (ev: MessageEvent<string>) => {
      let parsed: SyncEvent
      try {
        parsed = JSON.parse(ev.data) as SyncEvent
      } catch {
        return // malformed line (shouldn't happen) — skip rather than crash the feed
      }

      setEvents((prev) => {
        const next = prev.length >= MAX_EVENTS ? prev.slice(prev.length - MAX_EVENTS + 1) : prev.slice()
        next.push(parsed)
        // Persisting here (rather than in a separate effect watching `events`)
        // means only genuine incoming events write through to storage —
        // clear() below intentionally resets the in-memory view for a fresh
        // per-job feed without also discarding the persisted history that
        // other mounts of this hook (e.g. the dashboard) hydrate from.
        persistEvents(next)
        return next
      })

      setCounters((prev) => {
        if (parsed.kind === 'section' && PASS_STARTED_RE.test(parsed.message)) {
          return ZERO_COUNTERS
        }
        switch (parsed.kind) {
          case 'add':
            return { ...prev, added: prev.added + 1 }
          case 'remove':
            return { ...prev, removed: prev.removed + 1 }
          case 'hold':
            return { ...prev, held: prev.held + 1 }
          case 'miss':
            return { ...prev, missing: prev.missing + 1 }
          default:
            return prev
        }
      })
    }

    return () => source.close()
  }, [])

  const clear = useCallback(() => {
    setEvents([])
    setCounters(ZERO_COUNTERS)
  }, [])

  return { events, counters, connected, clear }
}
