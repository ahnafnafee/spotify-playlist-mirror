import { Fragment, useEffect, useState } from 'react'
import { LuArrowLeft, LuArrowRight, LuCheck, LuInfo } from 'react-icons/lu'

import { api, errorMessage } from '@/api'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { RadioCard } from '@/components/ui/RadioCard'
import { SelectField } from '@/components/ui/SelectField'
import { ServiceLogo } from '@/components/ui/ServiceLogo'
import { SettingsGroup } from '@/components/ui/SettingsGroup'
import { TextField } from '@/components/ui/TextField'
import { Toggle } from '@/components/ui/Toggle'
import { useSettings } from '@/hooks/useSettings'
import { cn } from '@/lib/cn'
import { serviceLogoId, tagDot, tagText } from '@/lib/constants'
import { isValidIntervalText, isValidNonNegativeInt, isValidPositiveInt } from '@/lib/format'
import { buildSyncSummaryRows, enabledProvidersOf, lockedSourceOf, syncPeersOf } from '@/lib/syncSummary'
import { PlaylistFilterField } from '../settings/PlaylistFilterField'
import type { Account, SyncJob, SyncJobUpsertRequest, SyncMode } from '@/types'

// Kept as strings locally (not the SyncJob numbers) so they compose directly
// with TextField and the existing string-based validators; converted to
// numbers only in the request built at save time.
interface JobFormState {
  name: string
  enabled: boolean
  mode: SyncMode
  source: string
  providers: string
  playlists: string
  interval: string
  max_adds: string
  max_removals: string
  apply_large_removals: boolean
  download: boolean
}

const NEW_JOB_DEFAULTS: JobFormState = {
  name: '',
  enabled: true,
  mode: 'oneway',
  source: 'spotify',
  providers: '',
  playlists: '',
  interval: '15m',
  max_adds: '200',
  max_removals: '25',
  apply_large_removals: false,
  download: false,
}

function formFromJob(job: SyncJob | null): JobFormState {
  if (!job) return NEW_JOB_DEFAULTS
  return {
    name: job.name,
    enabled: job.enabled,
    mode: job.mode,
    source: job.source,
    providers: job.providers,
    playlists: job.playlists,
    interval: job.interval,
    max_adds: String(job.max_adds),
    max_removals: String(job.max_removals),
    apply_large_removals: job.apply_large_removals,
    download: job.download,
  }
}

const INTERVAL_PRESETS: Array<{ value: string; label: string }> = [
  { value: '15m', label: 'Every 15 minutes' },
  { value: '30m', label: 'Every 30 minutes' },
  { value: '1h', label: 'Every hour' },
  { value: '2h', label: 'Every 2 hours' },
  { value: '3h', label: 'Every 3 hours' },
  { value: '6h', label: 'Every 6 hours' },
  { value: '12h', label: 'Every 12 hours' },
  { value: '24h', label: 'Once a day' },
]

// The wizard's five steps, in order. `intro` is the one friendly sentence
// shown above each step's fields; `label` is what the stepper shows.
const STEPS = [
  { label: 'Direction', intro: 'Which way changes flow between your services.' },
  { label: 'Services', intro: 'Which services to keep in sync.' },
  { label: 'Playlists', intro: 'Limit syncing to specific playlists, or leave empty to sync every same-named pair.' },
  { label: 'Schedule', intro: 'Run this sync on its own schedule, or only when you trigger it yourself.' },
  { label: 'Limits & downloads', intro: "Guardrails so one pass can't make a huge change, plus an optional offline copy of what's synced." },
] as const

/** A followers/services toggle chip — `locked` marks whichever service is
 * currently this job's sync source, which is always included and can't be
 * toggled off. */
function ProviderChip({
  account,
  checked,
  locked,
  onToggle,
}: {
  account: Account
  checked: boolean
  locked: boolean
  onToggle: () => void
}) {
  const logoId = serviceLogoId(account.id)
  const connected = account.state === 'connected'

  return (
    <button
      type="button"
      onClick={connected && !locked ? onToggle : undefined}
      disabled={!connected}
      aria-pressed={connected ? checked : undefined}
      title={
        !connected
          ? `Connect ${account.name} on the Accounts page to include it in syncing.`
          : locked
            ? `${account.name} is the sync source, always included, and it's never modified.`
            : undefined
      }
      className={cn(
        'inline-flex h-9 items-center gap-2 rounded-chip border-[1.5px] px-3 text-[13px] font-semibold transition-colors duration-fast',
        !connected
          ? 'cursor-not-allowed border-dashed border-border text-text-3 opacity-60'
          : checked
            ? cn('border-accent bg-accent-soft text-accent', locked && 'cursor-default')
            : 'border-border-strong text-text-2 hover:bg-surface-2',
      )}
    >
      {logoId ? (
        <ServiceLogo service={logoId} className={cn('size-4 shrink-0', connected && tagText(account.id))} />
      ) : (
        <span className={cn('size-2 shrink-0 rounded-full', tagDot(account.id))} aria-hidden="true" />
      )}
      {account.name}
      {locked && connected && (
        <span className="rounded-full bg-accent px-1.5 py-[1px] font-mono text-[9px] font-bold uppercase tracking-wide text-on-accent">
          source
        </span>
      )}
      {!connected && <span className="font-normal text-text-3">not connected</span>}
    </button>
  )
}

/** Single-select variant for the Direction step's "which provider is the
 * source of truth" picker — same visual language as ProviderChip, but
 * exclusive-choice (radio) rather than a toggle set. */
function SourceChip({ account, selected, onSelect }: { account: Account; selected: boolean; onSelect: () => void }) {
  const logoId = serviceLogoId(account.id)
  const connected = account.state === 'connected'

  return (
    <button
      type="button"
      role="radio"
      aria-checked={connected ? selected : undefined}
      onClick={connected ? onSelect : undefined}
      disabled={!connected}
      title={!connected ? `Connect ${account.name} on the Accounts page to choose it as the source.` : undefined}
      className={cn(
        'inline-flex h-9 items-center gap-2 rounded-chip border-[1.5px] px-3 text-[13px] font-semibold transition-colors duration-fast',
        !connected
          ? 'cursor-not-allowed border-dashed border-border text-text-3 opacity-60'
          : selected
            ? 'border-accent bg-accent-soft text-accent'
            : 'border-border-strong text-text-2 hover:bg-surface-2',
      )}
    >
      {logoId ? (
        <ServiceLogo service={logoId} className={cn('size-4 shrink-0', connected && tagText(account.id))} />
      ) : (
        <span className={cn('size-2 shrink-0 rounded-full', tagDot(account.id))} aria-hidden="true" />
      )}
      {account.name}
      {!connected && <span className="font-normal text-text-3">not connected</span>}
    </button>
  )
}

/** Compact numbered stepper — always fits the modal at any width (no
 * horizontal scroll): small circular markers connected by lines that
 * flex-grow to fill the row, with the label shown only for the current step
 * (as a caption below) rather than on every marker. This is a config people
 * revisit, not a linear onboarding wizard, so every marker stays clickable
 * regardless of visited state. */
function StepTabs({ current, visited, onJump }: { current: number; visited: Set<number>; onJump: (i: number) => void }) {
  return (
    <div className="flex flex-col gap-2">
      <div role="radiogroup" aria-label="Sync setup steps" className="flex items-center">
        {STEPS.map((s, i) => {
          const isCurrent = i === current
          const isVisited = visited.has(i) && !isCurrent
          return (
            <div key={s.label} className={cn('flex items-center', i < STEPS.length - 1 && 'flex-1')}>
              <button
                type="button"
                role="radio"
                aria-checked={isCurrent}
                aria-label={s.label}
                title={s.label}
                onClick={() => onJump(i)}
                className={cn(
                  'flex size-8 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-bold transition-colors duration-fast',
                  isCurrent
                    ? 'bg-accent text-on-accent ring-2 ring-accent/25'
                    : isVisited
                      ? 'bg-success-soft text-success hover:bg-success-soft/70'
                      : 'bg-surface-2 text-text-3 hover:bg-border',
                )}
              >
                {isVisited ? <LuCheck className="size-3.5" strokeWidth={3} aria-hidden="true" /> : i + 1}
              </button>
              {i < STEPS.length - 1 && (
                <span aria-hidden="true" className={cn('mx-1 h-px flex-1', i < current ? 'bg-success/50' : 'bg-border')} />
              )}
            </div>
          )
        })}
      </div>
      <p className="text-center font-mono text-[11px] font-semibold tracking-wide text-text-2">
        Step {current + 1} of {STEPS.length} · {STEPS[current].label}
      </p>
    </div>
  )
}

interface Props {
  open: boolean
  onClose: () => void
  /** null = creating a new sync job. */
  job: SyncJob | null
  accounts: Account[]
  onSaved: () => void
}

const FORM_ID = 'sync-wizard-form'

/** Create/edit a single named sync job — Direction (mode + one-way source),
 * Services (participating providers), Playlists, Schedule (this job's own
 * interval + active toggle), and Limits & downloads (safety caps + opting
 * into the global download mirror), ending with a plain-English review.
 * Saves via POST/PUT /api/syncs; the only /api/settings traffic is a
 * read-only fetch of the global download folder, purely to show it in the
 * review's Downloads row. */
export function SyncWizard({ open, onClose, job, accounts, onSaved }: Props) {
  const { settings } = useSettings()
  const [form, setForm] = useState<JobFormState>(NEW_JOB_DEFAULTS)
  const [step, setStep] = useState(0)
  const [visited, setVisited] = useState<Set<number>>(() => new Set([0]))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fresh state every time the wizard (re)opens, so a previous attempt (or a
  // different job) never leaks into a new session.
  useEffect(() => {
    if (!open) return
    setForm(formFromJob(job))
    setStep(0)
    setVisited(new Set([0]))
    setSaving(false)
    setError(null)
  }, [open, job])

  function goToStep(i: number) {
    setStep(i)
    setVisited((prev) => (prev.has(i) ? prev : new Set(prev).add(i)))
  }

  function setField<K extends keyof JobFormState>(key: K, value: JobFormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const syncPeers = syncPeersOf(accounts)
  const jellyfinConnected = accounts.some((a) => a.id === 'jellyfin' && a.state === 'connected')

  // The configurable one-way source of truth (default: spotify). Only
  // meaningful in one-way mode — N-way has no single source, so nothing is
  // locked there even if a non-default source was saved from an earlier
  // one-way session.
  const syncSource = form.source || 'spotify'
  const lockedSourceId = lockedSourceOf({ mode: form.mode, source: form.source })
  const nonSpotifySourceConflict =
    form.mode !== 'nway' && syncSource !== 'spotify' && (form.download || jellyfinConnected)

  const enabledProviders = enabledProvidersOf({ providers: form.providers }, syncPeers)

  // Step 3's playlist picker has to browse whichever provider is actually
  // meaningful for this job, not always Spotify (the picker's original,
  // single-sync-era default): the one-way source of truth in one-way mode,
  // or — N-way has no single source — Spotify if it's a participating peer,
  // else the first participating peer in syncPeers order. Recomputed on
  // every render, so going back to Direction/Services and changing the
  // source/participants immediately reflects here too.
  const playlistPickerProviderId =
    form.mode !== 'nway' ? syncSource : (enabledProviders.has('spotify') ? 'spotify' : syncPeers.find((a) => enabledProviders.has(a.id))?.id) || null

  function toggleProvider(id: string) {
    if (id === lockedSourceId) return // the source — never toggleable
    const next = new Set(enabledProviders)
    if (lockedSourceId) next.add(lockedSourceId) // materializing an explicit list must never drop the source
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setField('providers', [...next].join(','))
  }

  const nameValid = form.name.trim().length > 0
  const intervalValid = isValidIntervalText(form.interval)
  const maxAddsValid = isValidPositiveInt(form.max_adds)
  const maxRemovalsValid = isValidNonNegativeInt(form.max_removals)
  const formValid = nameValid && intervalValid && maxAddsValid && maxRemovalsValid

  // Only Direction's name (always valid) aside, Schedule (interval) and
  // Limits (caps) are the only steps with a bad state to block Next on.
  const stepValid = [true, true, true, intervalValid, maxAddsValid && maxRemovalsValid]
  const isLastStep = step === STEPS.length - 1

  const previewJob: SyncJob = {
    id: job?.id ?? '',
    name: form.name.trim() || 'This sync',
    enabled: form.enabled,
    mode: form.mode,
    source: form.source,
    providers: form.providers,
    playlists: form.playlists,
    interval: form.interval,
    max_adds: Number(form.max_adds) || 0,
    max_removals: Number(form.max_removals) || 0,
    apply_large_removals: form.apply_large_removals,
    download: form.download,
  }
  const summaryRows = buildSyncSummaryRows(previewJob, syncPeers, settings?.DOWNLOAD_DIR)

  async function handleSave() {
    if (!formValid) return
    setSaving(true)
    setError(null)
    try {
      const values: SyncJobUpsertRequest = {
        name: form.name.trim(),
        enabled: form.enabled,
        mode: form.mode,
        source: form.source,
        providers: form.providers,
        playlists: form.playlists,
        interval: form.interval,
        max_adds: Number(form.max_adds),
        max_removals: Number(form.max_removals),
        apply_large_removals: form.apply_large_removals,
        download: form.download,
      }
      if (job) await api.updateSync(job.id, values)
      else await api.createSync(values)
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={job ? `Edit "${job.name}"` : 'New sync'}
      description="A self-contained sync configuration: direction, services, playlists, schedule, and limits."
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" form={FORM_ID} loading={saving} disabled={!formValid}>
            {job ? 'Save changes' : 'Create sync'}
          </Button>
        </>
      }
    >
      <form
        id={FORM_ID}
        className="flex flex-col gap-4 py-1"
        onSubmit={(e) => {
          e.preventDefault()
          void handleSave()
        }}
      >
        {error && <p className="rounded-control bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}

        <TextField
          label="Name"
          help='Shown in your list of syncs, e.g. "Workout playlists" or "Family Spotify".'
          placeholder="e.g. Default"
          required
          value={form.name}
          onChange={(e) => setField('name', e.target.value)}
        />

        <StepTabs current={step} visited={visited} onJump={goToStep} />

        <SettingsGroup label={STEPS[step].label.toUpperCase()}>
          <p className="text-xs leading-relaxed text-text-3">{STEPS[step].intro}</p>

          {step === 0 && (
            <>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                <RadioCard
                  name="sync-mode"
                  value="oneway"
                  checked={form.mode !== 'nway'}
                  onChange={() => setField('mode', 'oneway')}
                  title="One-way →"
                  description="One provider is the source of truth. Everyone else follows it, and it's never modified."
                />
                <RadioCard
                  name="sync-mode"
                  value="nway"
                  checked={form.mode === 'nway'}
                  onChange={() => setField('mode', 'nway')}
                  title="Bidirectional (N-way) ⇄"
                  description="A track added or removed on any connected service propagates to all the others."
                />
              </div>

              {form.mode !== 'nway' && (
                <div className="flex flex-col gap-2.5 border-t border-border pt-3.5">
                  <div>
                    <span className="text-[12.5px] font-semibold text-text-2">Source of truth</span>
                    <p className="mt-1 text-xs leading-relaxed text-text-3">
                      This provider's playlists are the source of truth. Every other service follows it, and it's
                      never modified.
                    </p>
                  </div>
                  <div role="radiogroup" aria-label="Source of truth" className="flex flex-wrap gap-2">
                    {syncPeers.map((account) => (
                      <SourceChip
                        key={account.id}
                        account={account}
                        selected={syncSource === account.id}
                        onSelect={() => setField('source', account.id)}
                      />
                    ))}
                  </div>
                  {nonSpotifySourceConflict && (
                    <p className="flex items-start gap-1.5 text-xs leading-relaxed text-text-3">
                      <LuInfo className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                      Local downloads + Jellyfin covers currently require Spotify as the source, so they'll be
                      skipped.
                    </p>
                  )}
                </div>
              )}
            </>
          )}

          {step === 1 && (
            <div className="flex flex-wrap gap-2">
              {syncPeers.map((account) => (
                <ProviderChip
                  key={account.id}
                  account={account}
                  checked={account.id === lockedSourceId || enabledProviders.has(account.id)}
                  locked={account.id === lockedSourceId}
                  onToggle={() => toggleProvider(account.id)}
                />
              ))}
            </div>
          )}

          {step === 2 && (
            <PlaylistFilterField
              value={form.playlists}
              onChange={(v) => setField('playlists', v)}
              preferredProviderId={playlistPickerProviderId}
            />
          )}

          {step === 3 && (
            <>
              <Toggle
                checked={form.enabled}
                onChange={(v) => setField('enabled', v)}
                label="Active"
                description={
                  form.enabled
                    ? 'Runs on its own schedule, and is included in "Run all enabled".'
                    : 'Paused, skipped by its schedule and by "Run all enabled". You can still sync it manually.'
                }
              />
              <SelectField
                label="Interval"
                help="How often this sync runs automatically."
                value={form.interval}
                onChange={(e) => setField('interval', e.target.value)}
                options={
                  INTERVAL_PRESETS.some((o) => o.value === form.interval)
                    ? INTERVAL_PRESETS
                    : [{ value: form.interval, label: form.interval || '(unset)' }, ...INTERVAL_PRESETS]
                }
              />
            </>
          )}

          {step === 4 && (
            <>
              <div className="flex flex-col gap-3.5">
                <span className="text-[12.5px] font-semibold text-text-2">Safety caps</span>
                <div className="grid grid-cols-2 gap-3">
                  <TextField
                    label="Max additions / pass"
                    type="number"
                    min={1}
                    value={form.max_adds}
                    onChange={(e) => setField('max_adds', e.target.value)}
                    error={!maxAddsValid ? 'Enter a whole number of 1 or more.' : undefined}
                  />
                  <TextField
                    label="Max removals / pass"
                    type="number"
                    min={0}
                    value={form.max_removals}
                    onChange={(e) => setField('max_removals', e.target.value)}
                    error={!maxRemovalsValid ? 'Enter a whole number of 0 or more.' : undefined}
                  />
                </div>
                <div className="flex gap-2.5 rounded-control bg-warning-soft px-3.5 py-2.5">
                  <span className="font-mono text-xs font-semibold text-warning" aria-hidden="true">
                    ~
                  </span>
                  <p className="text-[12px] leading-relaxed text-text-2">
                    A pass that would exceed a cap <span className="font-semibold text-text">holds</span> the excess
                    instead of writing it. You'll see held rows in the feed and can review before anything is lost.
                  </p>
                </div>
                <Toggle
                  checked={form.apply_large_removals}
                  onChange={(v) => setField('apply_large_removals', v)}
                  label="Apply large removals"
                  description="Off (default): removals beyond the cap are held back for safety. On: they're deleted in capped batches over successive passes until cleared."
                />
              </div>

              <div className="border-t border-border pt-3.5">
                <Toggle
                  checked={form.download}
                  onChange={(v) => setField('download', v)}
                  label="Download this sync's playlists"
                  description="Uses the folder and format configured in Settings → Download mirror."
                />
              </div>

              <div className="flex flex-col gap-2.5 rounded-control border border-border bg-surface-2/40 p-3.5">
                <span className="font-mono text-[10px] font-semibold tracking-[0.1em] text-text-3">REVIEW</span>
                <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-2">
                  {summaryRows.map((row) => (
                    <Fragment key={row.label}>
                      <dt className="pt-px font-mono text-[10px] font-semibold uppercase tracking-wide text-text-3">{row.label}</dt>
                      <dd className="min-w-0 text-[13px] leading-relaxed text-text">{row.value}</dd>
                    </Fragment>
                  ))}
                </dl>
              </div>
            </>
          )}
        </SettingsGroup>

        <div className="flex items-center gap-2">
          {step > 0 && (
            <Button
              type="button"
              variant="secondary"
              icon={<LuArrowLeft className="size-4" aria-hidden="true" />}
              onClick={() => goToStep(step - 1)}
            >
              Back
            </Button>
          )}
          {!isLastStep && (
            <Button type="button" onClick={() => goToStep(step + 1)} disabled={!stepValid[step]} className="ml-auto">
              Next
              <LuArrowRight className="size-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </form>
    </Modal>
  )
}
