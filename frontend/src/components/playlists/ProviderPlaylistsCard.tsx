import { Link } from 'react-router-dom'

import type { ProviderPlaylistsEntry } from '@/hooks/useProviderPlaylists'
import { cn } from '@/lib/cn'
import { serviceLogoId, tagText } from '@/lib/constants'
import { formatTrackCount } from '@/lib/format'
import type { Account } from '@/types'

import { Card } from '../ui/Card'
import { CoverArt } from '../ui/CoverArt'
import { EmptyState } from '../ui/EmptyState'
import { LoadingStatus, Skeleton } from '../ui/Skeleton'
import { ServiceLogo } from '../ui/ServiceLogo'
import { StatusPill } from '../ui/StatusPill'
import { BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES, BUTTON_VARIANT_CLASSES } from '../ui/buttonStyles'

/** One provider's playlists for the Browse section. Handles all four states
 * explicitly: not connected, loading, errored, and loaded (possibly empty). */
export function ProviderPlaylistsCard({ account, entry }: { account: Account; entry: ProviderPlaylistsEntry | undefined }) {
  const connected = account.state === 'connected'
  const logoId = serviceLogoId(account.id)

  return (
    <Card className="flex flex-col gap-3 p-4 sm:p-5">
      {/* Stacked, not side-by-side — at the 4-across desktop breakpoint a
          longer name + pill ("YouTube Music" + "Needs reconnect") don't
          both fit on one line, and a flex row would either truncate a
          legible provider name or need finicky wrap tuning. Giving the
          title its own full-width line first avoids both. */}
      <div className="flex flex-col items-start gap-1.5">
        <div className="flex w-full items-center gap-2">
          {logoId && <ServiceLogo service={logoId} className={cn('size-4 shrink-0', tagText(account.id))} />}
          <h3 className="min-w-0 flex-1 truncate text-base font-bold text-text">{account.name}</h3>
          {connected && entry && entry.playlists.length > 0 && (
            <span className="shrink-0 font-mono text-[11px] text-text-3">
              {entry.playlists.length} playlist{entry.playlists.length === 1 ? '' : 's'}
            </span>
          )}
        </div>
        <StatusPill state={account.state} />
      </div>

      {!connected ? (
        <EmptyState
          className="py-6"
          title="Nothing to browse yet."
          description="Connect this service and its playlists appear here, ready for pairing."
          action={
            <Link to="/accounts" className={cn(BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES.sm, BUTTON_VARIANT_CLASSES.primary)}>
              Connect
            </Link>
          }
        />
      ) : !entry || (entry.loading && entry.playlists.length === 0) ? (
        <LoadingStatus label={`Loading ${account.name} playlists…`}>
          <div className="flex flex-col gap-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        </LoadingStatus>
      ) : entry.error ? (
        <p className="text-sm text-danger">Could not load playlists: {entry.error}</p>
      ) : entry.playlists.length > 0 ? (
        <ul className="thin-scrollbar flex max-h-80 flex-col divide-y divide-border overflow-y-auto">
          {entry.playlists.map((p, i) => (
            <li key={p.id} className="flex items-center gap-3 py-2">
              <span className="shrink-0 font-mono text-[10px] text-text-3" aria-hidden="true">
                {String(i + 1).padStart(2, '0')}
              </span>
              <CoverArt image={p.image} />
              <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-text">{p.name}</span>
              {formatTrackCount(p.count) && (
                <span className="shrink-0 font-mono text-[11.5px] text-text-3">{formatTrackCount(p.count)}</span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState className="py-6" title="No playlists found" description="This service doesn't have any playlists yet." />
      )}
    </Card>
  )
}
