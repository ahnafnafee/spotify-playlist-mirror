import { useState } from 'react'
import { LuPause, LuPlay, LuSquare } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { Button } from '@/components/ui/Button'

interface Props {
  jobId: string
  /** This job's pass is currently running — show Pause + Stop. */
  running: boolean
  /** Its last pass was paused — show Resume (re-runs; reconcile is idempotent). */
  paused: boolean
  /** A pause/stop already requested but not yet in effect — the buttons read
   * "Pausing…"/"Stopping…" until the pass halts at its next checkpoint. */
  pending: 'pause' | 'stop' | null
  onChanged: () => void
  onError?: (message: string) => void
}

/** Stop/Pause a running sync pass, or Resume a paused one — the mid-run
 * counterpart to the schedule on/off toggle. Shared by the Sync-page card and
 * the dashboard panel so both surfaces behave identically. An interrupt takes
 * effect at the next checkpoint (mid-playlist), so while it's pending the button
 * reflects that rather than looking like nothing happened. */
export function SyncControls({ jobId, running, paused, pending, onChanged, onError }: Props) {
  const [busy, setBusy] = useState(false)

  async function control(action: 'pause' | 'stop' | 'resume') {
    setBusy(true)
    try {
      if (action === 'pause') await api.pauseSyncJob(jobId)
      else if (action === 'stop') await api.stopSyncJob(jobId)
      else await api.resumeSyncJob(jobId)
      onChanged()
    } catch (err) {
      onError?.(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (running) {
    return (
      <>
        <Button
          variant="secondary"
          size="sm"
          disabled={busy || pending === 'pause'}
          icon={<LuPause className="size-3.5" aria-hidden="true" />}
          onClick={() => void control('pause')}
        >
          {pending === 'pause' ? 'Pausing…' : 'Pause'}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={busy || pending === 'stop'}
          icon={<LuSquare className="size-3.5" aria-hidden="true" />}
          onClick={() => void control('stop')}
        >
          {pending === 'stop' ? 'Stopping…' : 'Stop'}
        </Button>
      </>
    )
  }
  if (paused) {
    return (
      <Button
        variant="secondary"
        size="sm"
        disabled={busy}
        icon={<LuPlay className="size-3.5" aria-hidden="true" />}
        onClick={() => void control('resume')}
      >
        Resume
      </Button>
    )
  }
  return null
}
