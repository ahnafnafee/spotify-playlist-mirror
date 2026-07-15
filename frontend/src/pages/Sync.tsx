import { useMemo, useState } from 'react'
import { LuPlus } from 'react-icons/lu'

import { LiveFeed } from '@/components/dashboard/LiveFeed'
import { SyncJobCard } from '@/components/sync/SyncJobCard'
import { SyncWizard } from '@/components/sync/SyncWizard'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingStatus, Skeleton } from '@/components/ui/Skeleton'
import { useAccounts } from '@/hooks/useAccounts'
import { useSyncs } from '@/hooks/useSyncs'
import { useSyncStatus } from '@/hooks/useSyncStatus'
import { syncPeersOf } from '@/lib/syncSummary'
import type { SyncJob } from '@/types'

/** A list of independent, named sync jobs (Soundiiz-style) — each is a
 * self-contained configuration edited via the SyncWizard modal. The global
 * download mirror folder/format live on Settings; a job only opts in. */
export default function Sync() {
  const { syncs, loading, error, refresh } = useSyncs()
  const { accounts } = useAccounts()
  const { status, refresh: refreshStatus } = useSyncStatus()

  const [wizardOpen, setWizardOpen] = useState(false)
  const [editingJob, setEditingJob] = useState<SyncJob | null>(null)

  const peers = useMemo(() => syncPeersOf(accounts ?? []), [accounts])

  function openNew() {
    setEditingJob(null)
    setWizardOpen(true)
  }

  function openEdit(job: SyncJob) {
    setEditingJob(job)
    setWizardOpen(true)
  }

  function refreshAll() {
    void refresh()
    void refreshStatus()
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text sm:text-[22px]">Sync</h1>
          <p className="mt-1 text-sm text-text-3">
            Independent sync configurations, each running on its own schedule. The download folder is shared, set
            on the Settings page.
          </p>
        </div>
        <Button icon={<LuPlus className="size-4" aria-hidden="true" />} onClick={openNew}>
          New sync
        </Button>
      </div>

      {error && <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">Could not load syncs: {error}</p>}

      {loading && !syncs ? (
        <LoadingStatus label="Loading syncs…">
          <div className="flex flex-col gap-3">
            <Skeleton className="h-32 w-full rounded-card" />
            <Skeleton className="h-32 w-full rounded-card" />
          </div>
        </LoadingStatus>
      ) : syncs && syncs.length > 0 ? (
        <div className="flex flex-col gap-3">
          {syncs.map((job) => (
            <SyncJobCard
              key={job.id}
              job={job}
              peers={peers}
              running={status?.jobs.find((j) => j.id === job.id)?.running ?? false}
              queued={status?.jobs.find((j) => j.id === job.id)?.queued ?? false}
              paused={status?.jobs.find((j) => j.id === job.id)?.paused ?? false}
              pending={status?.jobs.find((j) => j.id === job.id)?.pending ?? null}
              onEdit={() => openEdit(job)}
              onChanged={refreshAll}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          title="No syncs yet"
          description="Create a sync to start mirroring playlists between your connected services."
          action={
            <Button icon={<LuPlus className="size-4" aria-hidden="true" />} onClick={openNew}>
              New sync
            </Button>
          }
        />
      )}

      {syncs && syncs.length > 0 && (
        <Card className="p-4 sm:p-5">
          <LiveFeed />
        </Card>
      )}

      <SyncWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        job={editingJob}
        accounts={accounts ?? []}
        onSaved={() => {
          setWizardOpen(false)
          refreshAll()
        }}
      />
    </div>
  )
}
