import { Link } from 'react-router-dom'
import { LuCircleAlert, LuTriangleAlert } from 'react-icons/lu'
import type { IconType } from 'react-icons'

import type { Account, SyncStatus } from '@/types'

import { BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES, BUTTON_VARIANT_CLASSES } from '../ui/buttonStyles'

interface NeedsLookItem {
  key: string
  icon: IconType
  title: string
  description: string
  action?: { label: string; to: string }
}

/** Every item here traces back to a real field — account state/detail, or the
 * last pass's own ok flag and per-target held/deferred/removals-skipped counts.
 * Nothing is invented (no fabricated "last synced" claims). */
function buildItems(accounts: Account[] | null, status: SyncStatus | null): NeedsLookItem[] {
  const items: NeedsLookItem[] = []

  for (const a of accounts ?? []) {
    if (a.state === 'expired') {
      items.push({
        key: `acct-${a.id}`,
        icon: LuTriangleAlert,
        title: `${a.name} sign-in expired`,
        description: a.detail || 'Reconnect to resume the syncs that touch it.',
        action: { label: 'Reconnect', to: '/accounts' },
      })
    } else if (a.state === 'error') {
      items.push({
        key: `acct-${a.id}`,
        icon: LuCircleAlert,
        title: `${a.name} connection error`,
        description: a.detail || 'Passes skip this service until the error clears.',
        action: { label: 'Fix', to: '/accounts' },
      })
    } else if (a.state === 'unconfigured') {
      items.push({
        key: `acct-${a.id}`,
        icon: LuTriangleAlert,
        title: `${a.name} isn't set up`,
        description: a.detail || "Connect it to include it in syncs. It's skipped until then.",
        action: { label: 'Connect', to: '/accounts' },
      })
    }
  }

  if (status?.last && !status.last.ok) {
    items.push({
      key: 'last-pass-error',
      icon: LuCircleAlert,
      title: 'The last pass failed',
      description: status.last.error || "It didn't complete successfully. The services it reached are unaffected.",
    })
  }

  const heldTotal = status?.last?.per_target.reduce((sum, t) => sum + t.held + t.deferred, 0) ?? 0
  if (heldTotal > 0) {
    items.push({
      key: 'held',
      icon: LuTriangleAlert,
      title: `${heldTotal} change${heldTotal === 1 ? '' : 's'} held from the last pass`,
      description: 'A service needs a follow-up pass — an unmatched track was kept, or additions exceeded the cap. Nothing was lost.',
      action: { label: 'Review caps', to: '/sync' },
    })
  }

  const removalsSkipped = status?.last?.per_target.reduce((sum, t) => sum + (t.removals_skipped ?? 0), 0) ?? 0
  if (removalsSkipped > 0) {
    items.push({
      key: 'removals-skipped',
      icon: LuTriangleAlert,
      title: `${removalsSkipped} removal${removalsSkipped === 1 ? '' : 's'} held back for safety`,
      description:
        'They exceeded the per-pass removal cap. Turn on "Apply large removals" for the sync to delete them in batches over the next passes.',
      action: { label: 'Open sync', to: '/sync' },
    })
  }

  return items
}

/** Real, actionable problems only — hidden entirely when there's nothing to
 * flag rather than showing an empty section. */
export function NeedsALook({ accounts, status }: { accounts: Account[] | null; status: SyncStatus | null }) {
  const items = buildItems(accounts, status)
  if (items.length === 0) return null

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2.5">
        <h2 className="text-base font-extrabold tracking-tight text-text">Needs a look</h2>
        <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-chip bg-warning-soft px-1.5 font-mono text-xs font-bold text-warning">
          {items.length}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {items.map((item) => (
          <div
            key={item.key}
            className="flex items-center gap-3 rounded-card border border-border border-l-[3px] border-l-warning bg-surface p-4 shadow-sm"
          >
            {/* Solid bg-warning + text-surface (not the soft-tinted bg-warning-soft
                text-warning used elsewhere), matching the app's other solid chips
                (e.g. the primary button's bg-accent text-on-accent) — an
                outline glyph in the same hue as a light fill is too
                low-contrast to read as anything but blank. --color-surface
                inverts appropriately per theme (light in light mode, dark in
                dark mode), so this stays legible against --color-warning's
                own per-theme fill in both. */}
            <span className="flex size-8 shrink-0 items-center justify-center rounded-control bg-warning text-surface">
              <item.icon className="size-[18px]" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold text-text">{item.title}</p>
              <p className="text-xs leading-relaxed text-text-2">{item.description}</p>
            </div>
            {item.action && (
              <Link
                to={item.action.to}
                className={`${BUTTON_BASE_CLASSES} ${BUTTON_SIZE_CLASSES.sm} ${BUTTON_VARIANT_CLASSES.primary} shrink-0`}
              >
                {item.action.label}
              </Link>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
