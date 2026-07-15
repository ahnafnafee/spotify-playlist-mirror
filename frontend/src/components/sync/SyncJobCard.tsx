import { useState } from 'react'
import { LuClock, LuPencil, LuTrash2 } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Spinner } from '@/components/ui/Spinner'
import { Toggle } from '@/components/ui/Toggle'
import { cn } from '@/lib/cn'
import { buildSyncSummaryRows } from '@/lib/syncSummary'
import type { Account, SyncJob } from '@/types'

import { SyncControls } from './SyncControls'
import { SyncRunButtons } from './SyncRunButtons'

interface Props {
  job: SyncJob
  peers: Account[]
  /** Whether this specific job is the one currently running, per
   * GET /api/sync/status's `jobs[].running` — guards against a double
   * "Sync now" and shows a live badge. */
  running: boolean
  /** Triggered but waiting behind the currently-running pass, per
   * `jobs[].queued` (passes are serialized — other jobs can still be
   * queued up while one runs). Shows a "Queued" badge and, like `running`,
   * guards against re-triggering this same job. */
  queued: boolean
  /** Its last pass was cut short by Pause, per `jobs[].paused` — shows a Resume
   * button (re-runs; reconcile is idempotent so it picks up the remainder). */
  paused: boolean
  /** While running, a pause/stop requested but not yet in effect ("Pausing…"). */
  pending: 'pause' | 'stop' | null
  onEdit: () => void
  onChanged: () => void
}

/** One row in the Sync page's list — name, a one-line plain-English recap,
 * its own interval, an immediate on/off toggle (PUT), Edit/Delete, and
 * Sync now/Preview (SyncRunButtons). Owns its own toggle/delete calls
 * (matching AccountCard's pattern) — the wizard (via Edit) is the only
 * place the job's actual config fields are changed; this card is for
 * at-a-glance management. */
export function SyncJobCard({ job, peers, running, queued, paused, pending, onEdit, onChanged }: Props) {
  const [togglingEnabled, setTogglingEnabled] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const summary = buildSyncSummaryRows(job, peers)
    .filter((r) => r.label !== 'Schedule')
    .map((r) => r.value)
    .join(' · ')

  async function toggleEnabled() {
    setTogglingEnabled(true)
    setError(null)
    try {
      await api.updateSync(job.id, { enabled: !job.enabled })
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setTogglingEnabled(false)
    }
  }

  async function handleDelete() {
    setDeleting(true)
    setError(null)
    try {
      await api.deleteSync(job.id)
      setConfirmingDelete(false)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-card border border-border bg-surface p-4 shadow-sm transition-opacity duration-fast sm:p-5',
        !job.enabled && 'opacity-80',
      )}
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-[15px] font-bold text-text">{job.name}</h3>
            {running && (
              <span className="inline-flex h-[22px] shrink-0 items-center gap-1.5 rounded-full bg-accent-soft px-2 text-[11px] font-semibold text-accent">
                <Spinner className="size-3 shrink-0" aria-hidden="true" />
                Running
              </span>
            )}
            {queued && !running && (
              <span className="inline-flex h-[22px] shrink-0 items-center gap-1.5 rounded-full bg-neutral-soft px-2 text-[11px] font-semibold text-neutral">
                <LuClock className="size-3 shrink-0" aria-hidden="true" />
                Queued
              </span>
            )}
            {!job.enabled && (
              <span className="inline-flex h-[22px] shrink-0 items-center rounded-full bg-neutral-soft px-2.5 text-[11.5px] font-semibold text-neutral">
                paused
              </span>
            )}
          </div>
          <p className="mt-1 text-[13px] leading-relaxed text-text-2">{summary}</p>
          <p className="mt-1.5 font-mono text-[10.5px] tracking-wide text-text-3">
            {job.enabled ? `every ${job.interval}` : 'manual only'}
          </p>
        </div>
        <Toggle
          checked={job.enabled}
          onChange={() => void toggleEnabled()}
          label={job.enabled ? `Pause "${job.name}"` : `Resume "${job.name}"`}
          hideLabel
          disabled={togglingEnabled}
        />
      </div>

      {error && <p className="text-xs text-danger">{error}</p>}

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <SyncRunButtons job={job} disabled={running || queued} onChanged={onChanged} />
        <SyncControls jobId={job.id} running={running} paused={paused} pending={pending} onChanged={onChanged} onError={setError} />
        <Button variant="secondary" size="sm" icon={<LuPencil className="size-3.5" aria-hidden="true" />} onClick={onEdit}>
          Edit
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          icon={<LuTrash2 className="size-3.5" aria-hidden="true" />}
          onClick={() => setConfirmingDelete(true)}
        >
          Delete
        </Button>
      </div>

      <ConfirmDialog
        open={confirmingDelete}
        title={`Delete "${job.name}"?`}
        description="This removes the sync configuration. Playlists and tracks already on each service are untouched."
        confirmLabel="Delete"
        danger
        loading={deleting}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmingDelete(false)}
      />
    </div>
  )
}
