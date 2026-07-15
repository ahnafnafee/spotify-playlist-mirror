import { Link } from 'react-router-dom'
import { LuArrowRight } from 'react-icons/lu'

import { SyncControls } from '@/components/sync/SyncControls'
import { SyncRunButtons } from '@/components/sync/SyncRunButtons'
import { formatClockTime } from '@/lib/format'
import { buildSyncSummaryRows, syncPeersOf } from '@/lib/syncSummary'
import type { Account, SyncJob, SyncStatus } from '@/types'

import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'

/** A job only shows a real clock time when it can actually fire on its own:
 * the master switch is on AND the job itself is enabled. Otherwise "Manual"
 * is the honest answer, regardless of whatever `next_run_at` the backend
 * last computed for it. */
function nextRunText(job: SyncJob, status: SyncStatus | null): string {
  if (!job.enabled || !status?.master) return 'Manual'
  const jobStatus = status.jobs.find((j) => j.id === job.id)
  return jobStatus?.next_run_at ? formatClockTime(jobStatus.next_run_at) : 'Not scheduled'
}

/** The dashboard's "what's configured to sync" panel — every job, its recap,
 * next run, and per-sync Sync now/Preview, so active syncs are visible
 * without a trip to the Sync page. Reuses SyncRunButtons + the same summary
 * builder as SyncJobCard so the two surfaces read identically. */
export function SyncsPanel({
  syncs,
  status,
  accounts,
  onChanged,
}: {
  syncs: SyncJob[] | null
  status: SyncStatus | null
  accounts: Account[] | null
  onChanged: () => void
}) {
  const peers = syncPeersOf(accounts ?? [])

  return (
    <Card className="flex flex-col overflow-hidden">
      <div className="flex items-center justify-between p-4">
        <h2 className="text-[15px] font-extrabold text-text">Syncs</h2>
        <Link to="/sync" className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-accent hover:text-accent-hover">
          Manage
          <LuArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
      </div>
      {syncs && syncs.length > 0 ? (
        <ul className="flex flex-col divide-y divide-border border-t border-border">
          {syncs.map((job) => {
            const summary = buildSyncSummaryRows(job, peers)
              .filter((r) => r.label !== 'Schedule')
              .map((r) => r.value)
              .join(' · ')
            const jobStatus = status?.jobs.find((j) => j.id === job.id)
            const running = jobStatus?.running ?? false
            const queued = jobStatus?.queued ?? false
            const paused = jobStatus?.paused ?? false
            const pending = jobStatus?.pending ?? null
            return (
              <li key={job.id} className="flex flex-col gap-2.5 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-[13.5px] font-semibold text-text">{job.name}</span>
                    {queued && !running && (
                      <span className="inline-flex h-[18px] shrink-0 items-center rounded-full bg-neutral-soft px-1.5 text-[10px] font-semibold text-neutral">
                        queued
                      </span>
                    )}
                    {!job.enabled && (
                      <span className="inline-flex h-[18px] shrink-0 items-center rounded-full bg-neutral-soft px-1.5 text-[10px] font-semibold text-neutral">
                        paused
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-text-3">{summary}</p>
                  <p className="mt-0.5 font-mono text-[10px] tracking-wide text-text-3">Next run: {nextRunText(job, status)}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <SyncRunButtons job={job} disabled={running || queued} onChanged={onChanged} />
                  <SyncControls jobId={job.id} running={running} paused={paused} pending={pending} onChanged={onChanged} />
                </div>
              </li>
            )
          })}
        </ul>
      ) : (
        <div className="px-4 pb-4">
          <EmptyState title="No syncs yet" description="Create a sync on the Sync page to start mirroring playlists." />
        </div>
      )}
    </Card>
  )
}
