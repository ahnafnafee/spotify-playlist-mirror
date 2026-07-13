import { LuCircleCheck, LuClock } from 'react-icons/lu'

import { formatDuration } from '@/lib/format'
import { cn } from '@/lib/cn'
import type { Account, SyncStatus } from '@/types'

interface HeroProps {
  accounts: Account[] | null
  status: SyncStatus | null
  /** Settings.DISPLAY_NAME — optional, user-set. Omitted from the greeting
   * entirely (not "Good evening, ") when blank. */
  displayName?: string
}

function timeOfDayGreeting(): string {
  const h = new Date().getHours()
  if (h < 5) return 'Good night'
  if (h < 12) return 'Good morning'
  if (h < 18) return 'Good afternoon'
  return 'Good evening'
}

function joinNames(names: string[]): string {
  if (names.length === 1) return names[0]
  if (names.length === 2) return `${names[0]} and ${names[1]}`
  return `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`
}

/** Plain-language summary of account health — never a fabricated "last
 * synced" claim, only what `useAccounts()` actually reports. */
function heroCopy(accounts: Account[] | null): { headline: string; detail: string } {
  if (!accounts || accounts.length === 0) {
    return { headline: 'Nothing connected yet.', detail: 'Connect a service on the Accounts page to get started.' }
  }
  const connected = accounts.filter((a) => a.state === 'connected')
  const problems = accounts.filter((a) => a.state !== 'connected')
  if (problems.length === 0) {
    return { headline: "Everything's in sync.", detail: `All ${accounts.length} of your connected services are up to date.` }
  }
  if (connected.length === 0) {
    return { headline: "Nothing's connected yet.", detail: 'Connect a service on the Accounts page to start syncing.' }
  }
  const names = joinNames(problems.map((p) => p.name))
  return {
    headline: "Almost everything's in sync.",
    detail: `${connected.length} of ${accounts.length} services are up to date. ${names} need${problems.length === 1 ? 's' : ''} attention — only the syncs that touch ${problems.length === 1 ? 'it' : 'them'} are affected.`,
  }
}

/** No per-pass "finished at" timestamp exists in the API (only how long the
 * pass took), so this reports what actually happened rather than a
 * fabricated "N min ago". */
function lastRunText(status: SyncStatus | null): string {
  if (!status?.last) return 'No sync has run yet'
  return `Last run ${status.last.execute ? 'applied changes' : 'was a preview'} · took ${formatDuration(status.last.duration_s)}`
}

/** The dashboard's opening read: "how are things" in one sentence, framed by
 * a time-of-day greeting. Running state swaps to a compact live indicator —
 * there's no per-track progress signal in the API, so unlike the mockup this
 * never claims a fake "N of M checked" percentage. */
export function Hero({ accounts, status, displayName }: HeroProps) {
  if (status?.running) {
    const previewing = status.mode === 'preview'
    return (
      <div className="flex flex-1 flex-col justify-center gap-3">
        <span className="inline-flex items-center gap-2 font-mono text-[11px] font-bold tracking-[0.14em] text-accent">
          <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden="true" />
          {previewing ? 'PREVIEWING NOW' : 'SYNCING NOW'}
        </span>
        <h1 className="text-display text-[26px] text-text sm:text-[32px]">
          {previewing ? 'Previewing your libraries…' : 'Syncing your libraries…'}
        </h1>
        <p className="flex items-center gap-2 text-sm text-text-2">
          <LuClock className="size-4 shrink-0 text-text-3" aria-hidden="true" />
          {previewing
            ? 'A dry run — checking what would change, without touching your libraries.'
            : 'You can leave this page — it keeps running in the background.'}
        </p>
      </div>
    )
  }

  const { headline, detail } = heroCopy(accounts)
  const connectedCount = accounts?.filter((a) => a.state === 'connected').length ?? 0
  const allUp = Boolean(accounts?.length) && connectedCount === accounts?.length

  return (
    <div className="flex flex-1 flex-col justify-center gap-3.5">
      <span className="font-mono text-[11px] font-bold tracking-[0.14em] text-text-3">
        {timeOfDayGreeting().toUpperCase()}
        {displayName?.trim() ? `, ${displayName.trim().toUpperCase()}` : ''}
      </span>
      <h1 className="max-w-[16ch] text-[32px] font-extrabold leading-[1.05] tracking-tight text-text sm:text-[40px]">{headline}</h1>
      <p className="max-w-[52ch] text-[15px] leading-relaxed text-text-2">{detail}</p>
      <div className="mt-0.5 flex flex-wrap items-center gap-3 text-[13px] text-text-3">
        <span className="inline-flex items-center gap-1.5">
          <LuClock className="size-[15px] shrink-0" aria-hidden="true" />
          {lastRunText(status)}
        </span>
        {accounts && accounts.length > 0 && (
          <>
            <span className="size-1 shrink-0 rounded-full bg-border-strong" aria-hidden="true" />
            <span className={cn('inline-flex items-center gap-1.5', allUp && 'text-success')}>
              <LuCircleCheck className="size-[15px] shrink-0" aria-hidden="true" />
              {connectedCount} of {accounts.length} up to date
            </span>
          </>
        )}
      </div>
    </div>
  )
}
