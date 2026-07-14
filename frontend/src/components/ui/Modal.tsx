import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { LuX } from 'react-icons/lu'

import { cn } from '@/lib/cn'

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  /** A fixed action row, docked on surface-2 below a border — e.g. Cancel +
   * Confirm. Omit for modals whose actions live inline in the body (the
   * connect wizard's per-step submit buttons). */
  footer?: ReactNode
  widthClassName?: string
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Accessible modal dialog: focus moves in on open, Tab/Shift+Tab are
 * trapped inside while open, Escape and an overlay click both close it, and
 * background scroll is locked. Rendered via a portal so it always stacks
 * above page content regardless of where it's used.
 *
 * Responsive shape: on phones/small tablets it renders as a near-full-width
 * bottom sheet (edge-to-edge, rounded top corners only, drag-handle
 * affordance, anchored to the bottom of the viewport) so it never fights a
 * narrow viewport for width; from `sm` up it's the conventional centered,
 * rounded dialog. Only the body scrolls — header and footer stay docked. */
export function Modal({ open, onClose, title, description, children, footer, widthClassName = 'max-w-lg' }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return

    const dialog = dialogRef.current
    dialog?.focus()

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (e.key !== 'Tab' || !dialog) return
      const focusable = dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-overlay sm:items-start sm:p-4 sm:pt-[max(4vh,1rem)]">
      <button type="button" aria-label="Close dialog" className="absolute inset-0" onClick={onClose} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        tabIndex={-1}
        className={cn(
          // Top-anchored (not vertically centered) on desktop: growing step
          // content only ever extends the dialog downward from a fixed top
          // edge, capping at max-h and scrolling the body from there, rather
          // than re-centering (and creeping the top edge up, eventually
          // above the viewport) on every content-height change. See
          // SyncWizard's Playlists step for the case that surfaced this.
          //
          // overflow-clip, not overflow-hidden: `hidden` still makes this a
          // valid scroll container for browser-driven scrolling (e.g. a
          // deeply nested item - a playlist checkbox scrolled well down its
          // own list - receiving focus and the browser walking every
          // scrollable ancestor to bring it into view), just without a
          // visible scrollbar or wheel response. That silently moved this
          // dialog's own scrollTop, shoving the header/footer out of place
          // while its outer box never budged. `clip` establishes no scroll
          // container at all, so only the body's own overflow-y-auto (and
          // the nested playlist list's) can ever actually scroll.
          'relative z-10 flex max-h-[92dvh] w-full flex-col overflow-clip rounded-t-modal border border-border-strong bg-surface shadow-lg outline-none',
          'sm:max-h-[90vh] sm:rounded-modal',
          widthClassName,
        )}
      >
        {/* Drag-handle affordance — visual only below sm; no real gesture is wired. */}
        <div className="flex justify-center pb-1 pt-2.5 sm:hidden">
          <span className="h-1 w-9 rounded-full bg-border-strong" aria-hidden="true" />
        </div>

        <div className="flex items-start justify-between gap-4 px-5 pb-4 pt-1 sm:px-6 sm:pt-5">
          <div className="min-w-0">
            <h2 id="modal-title" className="text-[15px] font-bold text-text">
              {title}
            </h2>
            {description && <p className="mt-1 text-sm text-text-2">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-control text-text-3 hover:bg-surface-2 hover:text-text-2 sm:size-7"
          >
            <LuX className="size-5" aria-hidden="true" />
          </button>
        </div>

        <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 sm:px-6', footer ? 'pb-4' : 'pb-5 sm:pb-6')}>
          {children}
        </div>

        {footer && (
          <div className="flex flex-col gap-3 border-t border-border bg-surface-2 px-5 py-3.5 sm:flex-row sm:justify-end sm:px-6">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
