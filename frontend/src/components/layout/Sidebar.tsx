import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LuArrowLeftRight,
  LuLayoutDashboard,
  LuLink2,
  LuListMusic,
  LuMenu,
  LuPanelLeftClose,
  LuPanelLeftOpen,
  LuRefreshCw,
  LuSettings2,
  LuX,
} from 'react-icons/lu'
import type { IconType } from 'react-icons'

import songmirrorMark from '@/assets/brand/songmirror-mark.png'
import { useSidebarCollapsed } from '@/hooks/useSidebarCollapsed'
import { cn } from '@/lib/cn'

const NAV_ITEMS: Array<{ to: string; label: string; end: boolean; icon: IconType }> = [
  { to: '/', label: 'Dashboard', end: true, icon: LuLayoutDashboard },
  { to: '/accounts', label: 'Accounts', end: false, icon: LuLink2 },
  { to: '/playlists', label: 'Playlists', end: false, icon: LuListMusic },
  { to: '/sync', label: 'Sync', end: false, icon: LuRefreshCw },
  { to: '/transfers', label: 'Transfers', end: false, icon: LuArrowLeftRight },
  { to: '/settings', label: 'Settings', end: false, icon: LuSettings2 },
]

/** 240px persistent rail from `lg` (1024px) up — collapsible to a 68px
 * icon-only rail (tooltip labels), state persisted to localStorage. Below
 * `lg`, the rail is replaced by a slim top bar whose hamburger opens the
 * same nav as a dropdown drawer. */
export function Sidebar() {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed()
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    if (!menuOpen) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    const mql = window.matchMedia('(min-width: 1024px)')
    function onBreakpointChange() {
      if (mql.matches) setMenuOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    mql.addEventListener('change', onBreakpointChange)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      mql.removeEventListener('change', onBreakpointChange)
    }
  }, [menuOpen])

  return (
    <>
      {/* Desktop rail */}
      <aside
        className={cn(
          'sticky top-0 hidden h-dvh shrink-0 flex-col overflow-y-auto border-r border-border bg-surface transition-[width] duration-base lg:flex',
          collapsed ? 'w-[68px]' : 'w-60',
        )}
      >
        <div
          className={cn(
            'flex h-16 shrink-0 items-center border-b border-border',
            collapsed ? 'justify-center px-2' : 'gap-2.5 px-4',
          )}
        >
          {collapsed ? (
            <button
              type="button"
              onClick={toggleCollapsed}
              title="Expand sidebar"
              aria-label="Expand sidebar"
              className="group grid size-9 place-items-center rounded-control transition-colors duration-fast hover:bg-surface-2"
            >
              {/* logo by default; the expand affordance cross-fades in on hover */}
              <span className="col-start-1 row-start-1 transition-opacity duration-fast group-hover:opacity-0">
                <Logo />
              </span>
              <LuPanelLeftOpen
                className="col-start-1 row-start-1 size-[18px] text-text-2 opacity-0 transition-opacity duration-fast group-hover:opacity-100"
                aria-hidden="true"
              />
            </button>
          ) : (
            <>
              <Logo />
              <span className="truncate text-[15px] font-extrabold tracking-tight text-text">SongMirror</span>
              <button
                type="button"
                onClick={toggleCollapsed}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
                className="ml-auto flex size-9 shrink-0 items-center justify-center rounded-control text-text-2 transition-colors duration-fast hover:bg-surface-2 hover:text-text"
              >
                <LuPanelLeftClose className="size-[18px]" aria-hidden="true" />
              </button>
            </>
          )}
        </div>

        <nav aria-label="Primary" className="flex flex-1 flex-col gap-1 p-3">
          {!collapsed && (
            <span className="px-2.5 pb-1.5 font-mono text-[10px] font-bold tracking-[0.14em] text-text-3">MENU</span>
          )}
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={collapsed ? item.label : undefined}
              aria-label={collapsed ? item.label : undefined}
              className={({ isActive }) =>
                cn(
                  'flex h-10 items-center gap-2.5 rounded-[9px] text-sm font-medium transition-colors duration-fast',
                  collapsed ? 'justify-center px-0' : 'px-3',
                  isActive
                    ? 'bg-accent-soft font-semibold text-accent shadow-[inset_2px_0_0_var(--color-accent)]'
                    : 'text-text-2 hover:bg-surface-2 hover:text-text',
                )
              }
            >
              <item.icon className="size-[18px] shrink-0" aria-hidden="true" />
              {!collapsed && item.label}
            </NavLink>
          ))}
        </nav>

      </aside>

      {/* Mobile: slim top bar + dropdown drawer */}
      <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-2.5 border-b border-border bg-surface/90 px-4 backdrop-blur lg:hidden">
        <Logo />
        <span className="truncate text-[14.5px] font-extrabold tracking-tight text-text">SongMirror</span>
        <button
          type="button"
          className="ml-auto inline-flex size-11 shrink-0 items-center justify-center rounded-control border border-border bg-surface-2 text-text"
          aria-expanded={menuOpen}
          aria-controls="mobile-nav"
          aria-label={menuOpen ? 'Close menu' : 'Open menu'}
          onClick={() => setMenuOpen((v) => !v)}
        >
          {menuOpen ? <LuX className="size-5" aria-hidden="true" /> : <LuMenu className="size-5" aria-hidden="true" />}
        </button>
      </header>

      {menuOpen && (
        <nav
          id="mobile-nav"
          aria-label="Primary"
          className="sticky top-14 z-40 flex flex-col gap-0.5 border-b border-border-strong bg-surface px-3 pb-4 pt-2.5 lg:hidden"
        >
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex h-12 items-center gap-3 rounded-[9px] pl-3.5 text-[15px] font-medium transition-colors duration-fast',
                  isActive ? 'bg-accent-soft font-semibold text-accent' : 'text-text-2 hover:bg-surface-2',
                )
              }
            >
              <item.icon className="size-[18px] shrink-0" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>
      )}
    </>
  )
}

function Logo() {
  // Orange mark on a transparent background — theme-agnostic, no light/dark
  // swap needed.
  return <img src={songmirrorMark} alt="" className="size-7 shrink-0" />
}
