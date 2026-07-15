import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'songmirror-sidebar-collapsed'

/** Persisted collapse state for the desktop sidebar rail — expanded by
 * default (mirrors useDarkMode's persistence pattern, but with no OS-level
 * preference to fall back to: absent/invalid storage just means expanded). */
export function useSidebarCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState<boolean>(() => window.localStorage.getItem(STORAGE_KEY) === 'true')

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, collapsed ? 'true' : 'false')
  }, [collapsed])

  const toggle = useCallback(() => setCollapsed((c) => !c), [])

  return [collapsed, toggle]
}
