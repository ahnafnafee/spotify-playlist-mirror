import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'songmirror-theme'

function getInitialDark(): boolean {
  // Dark by default, irrespective of the OS preference; a stored 'light' wins.
  return window.localStorage.getItem(STORAGE_KEY) !== 'light'
}

/** Class-based dark mode (see the `@custom-variant dark` in index.css). Dark is
 * the default on first visit; a persisted user override wins. The pre-React boot
 * script in index.html applies the same rule to avoid a flash of light. */
export function useDarkMode(): [boolean, () => void] {
  const [dark, setDark] = useState<boolean>(getInitialDark)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    window.localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light')
  }, [dark])

  const toggle = useCallback(() => setDark((d) => !d), [])

  return [dark, toggle]
}
