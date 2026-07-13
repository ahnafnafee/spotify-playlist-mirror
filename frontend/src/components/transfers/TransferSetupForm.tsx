import { useEffect, useMemo, useState } from 'react'

import { api, errorMessage } from '@/api'
import type { ProviderPlaylistsEntry } from '@/hooks/useProviderPlaylists'
import { cn } from '@/lib/cn'
import { serviceLogoId, tagLabel, tagText } from '@/lib/constants'
import type { Account, ProviderPlaylist, StartTransferRequest } from '@/types'

import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { ConfirmDialog } from '../ui/ConfirmDialog'
import { PlaylistPickerField } from '../ui/PlaylistPickerField'
import { Segmented } from '../ui/Segmented'
import { SelectField } from '../ui/SelectField'
import { ServiceLogo } from '../ui/ServiceLogo'
import { TextField } from '../ui/TextField'

interface Props {
  /** Connected accounts only — a transfer can't read from or write to a
   * disconnected service. */
  accounts: Account[]
  entries: Record<string, ProviderPlaylistsEntry>
  onStarted: (jobId: string) => void
}

const DEST_MODE_OPTIONS = [
  { value: 'existing', label: 'Existing playlist' },
  { value: 'create', label: 'Create new' },
]

/** A provider id's brand mark, tinted with its identity color — undefined
 * (no icon) for an unset or unrecognized id. */
function serviceIcon(providerId: string) {
  const logoId = serviceLogoId(providerId)
  return logoId ? <ServiceLogo service={logoId} className={`size-4 ${tagText(providerId)}`} /> : undefined
}

/** A transfer can only ever read from a playlist the connected account owns
 * — Spotify's API 403s on tracks for a merely-followed playlist. Only the
 * source deck needs this; copying INTO a followed/other playlist is fine. */
function sourceDisabledReason(p: ProviderPlaylist): string | undefined {
  return p.owned === false ? 'Not transferable' : undefined
}

export function TransferSetupForm({ accounts, entries, onStarted }: Props) {
  const [sourceProvider, setSourceProvider] = useState('')
  const [sourcePlaylistId, setSourcePlaylistId] = useState('')
  const [destProvider, setDestProvider] = useState('')
  const [destMode, setDestMode] = useState<'existing' | 'create'>('existing')
  const [destPlaylistId, setDestPlaylistId] = useState('')
  const [destName, setDestName] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const destProviderOptions = useMemo(() => accounts.filter((a) => a.id !== sourceProvider), [accounts, sourceProvider])

  // A source change invalidates a same-provider destination selection —
  // clear it rather than let a stale, now-hidden option linger.
  useEffect(() => {
    if (destProvider && destProvider === sourceProvider) {
      setDestProvider('')
      setDestPlaylistId('')
    }
  }, [sourceProvider, destProvider])

  // Default "create new"'s name to the source playlist's name — re-derives
  // whenever the source playlist or the create-new choice changes, but a
  // manual edit in between sticks until one of those changes again.
  useEffect(() => {
    if (destMode !== 'create') return
    const sourcePlaylist = entries[sourceProvider]?.playlists.find((p) => p.id === sourcePlaylistId)
    if (sourcePlaylist) setDestName(sourcePlaylist.name)
  }, [destMode, sourceProvider, sourcePlaylistId, entries])

  const sourcePlaylist = entries[sourceProvider]?.playlists.find((p) => p.id === sourcePlaylistId)
  const destPlaylist = destMode === 'existing' ? entries[destProvider]?.playlists.find((p) => p.id === destPlaylistId) : undefined

  const formValid = Boolean(
    sourceProvider &&
      sourcePlaylistId &&
      sourcePlaylist?.owned !== false &&
      destProvider &&
      (destMode === 'create' ? destName.trim() : destPlaylistId),
  )

  async function handleStart() {
    setStarting(true)
    setError(null)
    try {
      const body: StartTransferRequest = {
        source_provider: sourceProvider,
        source_playlist_id: sourcePlaylistId,
        dest_provider: destProvider,
        dest_playlist_id: destMode === 'create' ? null : destPlaylistId,
        dest_name: destMode === 'create' ? destName.trim() : (destPlaylist?.name ?? ''),
      }
      const res = await api.startTransfer(body)
      setConfirming(false)
      onStarted(res.job_id)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setStarting(false)
    }
  }

  return (
    <Card className="flex flex-col gap-5 p-4 sm:p-6">
      <div>
        <h2 className="text-sm font-bold text-text">Set up a transfer</h2>
        <p className="mt-1 text-xs text-text-3">
          A one-off copy — existing tracks on the destination are kept, this only adds.
        </p>
      </div>

      {accounts.length < 2 ? (
        <p className="text-sm text-text-3">
          Connect at least 2 services on the Accounts page to copy a playlist between them.
        </p>
      ) : (
        <>
          <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-stretch">
            {/* "Deck A" — twin tape decks patched by a dashed cable is the
                mental model: this one ends in a counter readout once a
                playlist is picked. */}
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-card border border-border-strong bg-inset shadow-sm">
              <div
                className="border-b border-border px-4 py-2"
                style={{ backgroundImage: 'radial-gradient(var(--color-border) 1px, transparent 1px)', backgroundSize: '9px 9px' }}
              >
                <span className="rounded bg-inset px-2 py-0.5 font-mono text-[10px] font-bold tracking-[0.14em] text-text-2">
                  DECK A — SOURCE
                </span>
              </div>
              <div className="flex flex-1 flex-col gap-3.5 p-4">
                <SelectField
                  label="Service"
                  icon={serviceIcon(sourceProvider)}
                  options={[{ value: '', label: 'Choose a service…' }, ...accounts.map((a) => ({ value: a.id, label: a.name }))]}
                  value={sourceProvider}
                  onChange={(e) => {
                    setSourceProvider(e.target.value)
                    setSourcePlaylistId('')
                  }}
                />
                <PlaylistPickerField
                  label="Playlist"
                  playlists={entries[sourceProvider]?.playlists ?? []}
                  loading={entries[sourceProvider]?.loading}
                  value={sourcePlaylistId}
                  disabled={!sourceProvider}
                  onChange={setSourcePlaylistId}
                  optionDisabledReason={sourceDisabledReason}
                />
              </div>
              {sourcePlaylist && (
                <div className="flex items-baseline gap-2.5 border-t border-border px-4 py-2.5">
                  <span className="font-mono text-[26px] font-bold leading-none tracking-wide text-accent">
                    {sourcePlaylist.count ?? '—'}
                  </span>
                  <span className="font-mono text-[9px] tracking-[0.1em] text-text-3">
                    {sourcePlaylist.count === null ? 'TRACK COUNT UNAVAILABLE' : 'TRACKS · SNAPSHOT AT COPY TIME'}
                  </span>
                </div>
              )}
            </div>

            {/* The dashed cable — a one-off patch, not a pairing. */}
            <div className="flex shrink-0 items-center justify-center gap-1.5 self-center">
              <span className="hidden h-0 w-6 border-t-2 border-dashed border-border-strong sm:block" aria-hidden="true" />
              <span
                aria-hidden="true"
                className="flex size-9 shrink-0 rotate-90 items-center justify-center rounded-full border border-border-strong bg-surface-2 text-[15px] font-semibold text-accent sm:size-10 sm:rotate-0 sm:text-[17px]"
              >
                →
              </span>
              <span className="hidden h-0 w-6 border-t-2 border-dashed border-border-strong sm:block" aria-hidden="true" />
            </div>

            {/* "Deck B" — ends in a write-mode lamp instead of a counter. */}
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-card border border-border-strong bg-inset shadow-sm">
              <div
                className="border-b border-border px-4 py-2"
                style={{ backgroundImage: 'radial-gradient(var(--color-border) 1px, transparent 1px)', backgroundSize: '9px 9px' }}
              >
                <span className="rounded bg-inset px-2 py-0.5 font-mono text-[10px] font-bold tracking-[0.14em] text-text-2">
                  DECK B — DESTINATION
                </span>
              </div>
              <div className="flex flex-1 flex-col gap-3.5 p-4">
                <SelectField
                  label="Service"
                  help={!sourceProvider ? 'Pick a source service first.' : undefined}
                  icon={serviceIcon(destProvider)}
                  options={[
                    { value: '', label: 'Choose a service…' },
                    ...destProviderOptions.map((a) => ({ value: a.id, label: a.name })),
                  ]}
                  value={destProvider}
                  disabled={!sourceProvider}
                  onChange={(e) => {
                    setDestProvider(e.target.value)
                    setDestPlaylistId('')
                  }}
                />

                <div className="flex flex-col gap-1.5">
                  <span className="text-[12.5px] font-semibold text-text-2">Playlist</span>
                  <Segmented
                    ariaLabel="Destination playlist"
                    options={DEST_MODE_OPTIONS}
                    value={destMode}
                    onChange={(v) => setDestMode(v as 'existing' | 'create')}
                  />
                </div>

                {destMode === 'existing' ? (
                  <PlaylistPickerField
                    label="Existing playlist"
                    placeholder={destProvider ? 'Choose a playlist…' : 'Choose a destination service first'}
                    playlists={entries[destProvider]?.playlists ?? []}
                    loading={entries[destProvider]?.loading}
                    value={destPlaylistId}
                    disabled={!destProvider}
                    onChange={setDestPlaylistId}
                  />
                ) : (
                  <TextField
                    label="New playlist name"
                    help="Defaults to the source playlist's name — feel free to change it."
                    required
                    value={destName}
                    onChange={(e) => setDestName(e.target.value)}
                  />
                )}
              </div>
              <div className="flex items-center gap-2 border-t border-border px-4 py-2.5">
                <span
                  className={cn('size-[7px] shrink-0 rounded-full', destMode === 'create' ? 'bg-warning' : 'bg-success')}
                  aria-hidden="true"
                />
                <span className="font-mono text-[9px] tracking-[0.1em] text-text-3">
                  {destMode === 'create' ? 'WRITE MODE — CREATE NEW · NAME FROM DECK A' : 'WRITE MODE — ADD TO EXISTING'}
                </span>
              </div>
            </div>
          </div>

          {error && <p className="text-sm text-danger">{error}</p>}

          <div>
            <Button onClick={() => setConfirming(true)} disabled={!formValid}>
              Copy playlist
            </Button>
          </div>
        </>
      )}

      <ConfirmDialog
        open={confirming}
        title="Copy this playlist?"
        description={
          sourcePlaylist
            ? `"${sourcePlaylist.name}" will be copied from ${tagLabel(sourceProvider)} to ${
                destMode === 'create'
                  ? `a new playlist named "${destName.trim()}"`
                  : `"${destPlaylist?.name ?? ''}"`
              } on ${tagLabel(destProvider)}. Existing tracks on the destination are kept — this only adds.`
            : 'This will start copying the selected playlist.'
        }
        confirmLabel="Copy playlist"
        loading={starting}
        onConfirm={() => void handleStart()}
        onCancel={() => setConfirming(false)}
      />
    </Card>
  )
}
