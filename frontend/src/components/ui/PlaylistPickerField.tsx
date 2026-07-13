import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { LuCheck, LuChevronDown, LuSearch } from 'react-icons/lu'

import { cn } from '@/lib/cn'
import { formatTrackCount } from '@/lib/format'
import type { ProviderPlaylist } from '@/types'

import { CoverArt } from './CoverArt'

/** Below this many options the list never needs a search box — it's already
 * scannable at a glance. Matches PlaylistFilterField's own threshold. */
const SEARCH_THRESHOLD = 8

interface PanelPosition {
  top: number
  left: number
  width: number
}

interface PlaylistPickerFieldProps {
  label: string
  help?: string
  playlists: ProviderPlaylist[]
  /** Still fetching — shows a "Loading…" placeholder instead of "Choose a
   * playlist…" while `playlists` is empty. */
  loading?: boolean
  /** Selected playlist id, or "" for none. */
  value: string
  onChange: (id: string) => void
  disabled?: boolean
  placeholder?: string
  /** Per-option override: return a short reason (e.g. "Not transferable") to
   * grey the option out and block selecting it, or `undefined` to leave it
   * selectable. Omit entirely for pickers where every fetched playlist is
   * always selectable (e.g. the transfer destination deck). */
  optionDisabledReason?: (playlist: ProviderPlaylist) => string | undefined
}

/** A single-select playlist picker that — unlike a native `<select>` — can
 * show each option's cover art. The panel is portaled to `document.body` and
 * positioned from the trigger's own bounding rect so it's never clipped by
 * an `overflow-hidden` ancestor (the transfer "deck" cards are one). Closes
 * on Escape, an outside click, or scroll/resize (simpler and just as usable
 * as continuously repositioning). */
export function PlaylistPickerField({
  label,
  help,
  playlists,
  loading,
  value,
  onChange,
  disabled,
  placeholder = 'Choose a playlist…',
  optionDisabledReason,
}: PlaylistPickerFieldProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [panelPos, setPanelPos] = useState<PanelPosition | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const fieldId = useId()
  const listboxId = `${fieldId}-listbox`

  const selected = useMemo(() => playlists.find((p) => p.id === value), [playlists, value])

  const filtered = useMemo(() => {
    if (!search.trim()) return playlists
    const q = search.trim().toLowerCase()
    return playlists.filter((p) => p.name.toLowerCase().includes(q))
  }, [playlists, search])

  function openPanel() {
    if (disabled) return
    const rect = triggerRef.current?.getBoundingClientRect()
    if (rect) setPanelPos({ top: rect.bottom + window.scrollY + 4, left: rect.left + window.scrollX, width: rect.width })
    setSearch('')
    setOpen(true)
  }

  function closePanel(refocus: boolean) {
    setOpen(false)
    if (refocus) triggerRef.current?.focus()
  }

  function select(id: string) {
    onChange(id)
    closePanel(true)
  }

  // Outside click and Escape close the panel; scroll/resize (of anything,
  // not just this panel) reposition it instead of closing it — a stray
  // incidental scroll (or, e.g., a screenshot tool that scrolls the page to
  // capture it) shouldn't slam a still-open picker shut.
  useEffect(() => {
    if (!open) return
    function reposition() {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (rect) setPanelPos({ top: rect.bottom + window.scrollY + 4, left: rect.left + window.scrollX, width: rect.width })
    }
    function onPointerDown(e: PointerEvent) {
      const target = e.target as Node
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return
      closePanel(false)
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') closePanel(true)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('scroll', reposition, true)
      window.removeEventListener('resize', reposition)
    }
  }, [open])

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={fieldId} className="text-[12.5px] font-semibold text-text-2">
        {label}
      </label>
      <button
        ref={triggerRef}
        id={fieldId}
        type="button"
        disabled={disabled}
        onClick={() => (open ? closePanel(true) : openPanel())}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        className={cn(
          'flex h-11 w-full items-center gap-2.5 rounded-control border border-border-strong bg-field px-2.5 text-left text-base text-text',
          'focus:border-accent focus:outline-none disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-text-3',
          'md:h-[42px] md:text-sm',
          open && 'border-accent',
        )}
      >
        {selected ? (
          <>
            <CoverArt image={selected.image} />
            <span className="min-w-0 flex-1 truncate">{selected.name}</span>
            {formatTrackCount(selected.count) && (
              <span className="shrink-0 font-mono text-[11px] text-text-3">{formatTrackCount(selected.count)}</span>
            )}
          </>
        ) : (
          <span className="min-w-0 flex-1 truncate text-text-3">{loading ? 'Loading…' : placeholder}</span>
        )}
        <LuChevronDown
          className={cn('size-4 shrink-0 text-text-3 transition-transform duration-fast', open && 'rotate-180')}
          aria-hidden="true"
        />
      </button>
      {help && <p className="text-xs text-text-3">{help}</p>}

      {open &&
        panelPos &&
        createPortal(
          <div
            ref={panelRef}
            style={{ position: 'absolute', top: panelPos.top, left: panelPos.left, width: panelPos.width }}
            className="z-50 flex flex-col gap-1.5 rounded-card border border-border-strong bg-surface p-2 shadow-lg"
          >
            {playlists.length > SEARCH_THRESHOLD && (
              <div className="relative">
                <LuSearch className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-text-3" aria-hidden="true" />
                <input
                  type="text"
                  autoFocus
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search playlists…"
                  aria-label="Search playlists"
                  className="h-10 w-full rounded-control border border-border-strong bg-field pl-9 pr-3 text-sm text-text placeholder:text-text-3 focus:border-accent focus:outline-none"
                />
              </div>
            )}
            <div id={listboxId} role="listbox" aria-label={label} className="thin-scrollbar flex max-h-56 flex-col gap-0.5 overflow-y-auto">
              {filtered.length === 0 ? (
                <p className="px-2 py-3 text-center text-xs text-text-3">No playlists match "{search}".</p>
              ) : (
                filtered.map((p) => {
                  const reason = optionDisabledReason?.(p)
                  return (
                    <button
                      key={p.id}
                      type="button"
                      role="option"
                      aria-selected={p.id === value}
                      aria-disabled={reason ? true : undefined}
                      disabled={Boolean(reason)}
                      title={reason}
                      onClick={() => select(p.id)}
                      className={cn(
                        'flex items-center gap-2.5 rounded-control px-2 py-1.5 text-left transition-colors duration-fast',
                        reason
                          ? 'cursor-not-allowed opacity-50'
                          : p.id === value
                            ? 'bg-accent-soft'
                            : 'hover:bg-surface-2',
                      )}
                    >
                      <CoverArt image={p.image} />
                      <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-text">{p.name}</span>
                      {reason ? (
                        <span className="shrink-0 font-mono text-[10.5px] text-text-3">{reason}</span>
                      ) : (
                        formatTrackCount(p.count) && (
                          <span className="shrink-0 font-mono text-[11px] text-text-3">{formatTrackCount(p.count)}</span>
                        )
                      )}
                      {p.id === value && <LuCheck className="size-3.5 shrink-0 text-accent" aria-hidden="true" />}
                    </button>
                  )
                })
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  )
}
