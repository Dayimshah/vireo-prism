import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

/**
 * Theme state.
 *
 * Three values, not two: `system` is a real choice that follows the OS and keeps following
 * it when the reader changes it, which a resolved light/dark pair cannot express.
 *
 * Coupled to the inline script in `index.html`
 * -------------------------------------------
 * That script reads the same {@link STORAGE_KEY} and applies the same `dark` class before
 * first paint, because React cannot: the bundle is fetched, parsed and mounted after the
 * browser has already painted, so a dark-mode reader would get a white flash on every
 * load. The two must agree on the key and the class name — if they drift, the flash
 * returns and nothing errors.
 */

/** Shared with the inline bootstrap script in `index.html`. */
const STORAGE_KEY = 'prism.theme'

/** What the reader chose. */
export type ThemePreference = 'light' | 'dark' | 'system'

/** What is actually on screen once `system` has been resolved. */
export type ResolvedTheme = 'light' | 'dark'

interface ThemeContextValue {
  /** The reader's choice, including `system`. */
  preference: ThemePreference
  /** The theme in effect right now. */
  resolved: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
  /**
   * Flip between light and dark.
   *
   * Resolves `system` first, so toggling from a system-dark default gives light — the
   * visible change a reader expects, rather than a no-op that stays dark.
   */
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

const MEDIA_QUERY = '(prefers-color-scheme: dark)'

/**
 * Read the stored preference.
 *
 * Every `localStorage` access in this module is wrapped: it *throws* rather than returning
 * null when cookies are blocked or the page is embedded in a partitioned third-party
 * context. An unhandled throw here would break the provider and take the whole app down
 * over a colour preference.
 */
function readStoredPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // Storage unavailable. `system` is the right default — it is what the inline script
    // falls back to as well.
  }
  return 'system'
}

/** Current OS preference. */
function systemTheme(): ResolvedTheme {
  return window.matchMedia(MEDIA_QUERY).matches ? 'dark' : 'light'
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference)

  // Tracked as state rather than read during render, so a change to the OS setting
  // re-renders. Reading `matchMedia` inline would give a value React never invalidates.
  const [systemResolved, setSystemResolved] = useState<ResolvedTheme>(systemTheme)

  useEffect(() => {
    const media = window.matchMedia(MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => {
      setSystemResolved(event.matches ? 'dark' : 'light')
    }
    media.addEventListener('change', onChange)
    return () => {
      media.removeEventListener('change', onChange)
    }
  }, [])

  const resolved: ResolvedTheme = preference === 'system' ? systemResolved : preference

  // Applied in an effect rather than during render: mutating `documentElement` while
  // rendering is a side effect React may run twice in StrictMode, and the class is
  // idempotent only because `toggle` is the sole writer.
  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', resolved === 'dark')
    // Tells the browser to render form controls and scrollbars to match. Without it a
    // native `<input type="date">` — which is what the window picker uses — renders its
    // calendar panel in light colours over a dark page.
    root.style.colorScheme = resolved
  }, [resolved])

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // The choice still applies for this session; it just will not survive a reload.
    }
  }, [])

  const toggle = useCallback(() => {
    setPreferenceState((current) => {
      const next: ThemePreference =
        current === 'system' ? (systemTheme() === 'dark' ? 'light' : 'dark') : current === 'dark' ? 'light' : 'dark'
      try {
        localStorage.setItem(STORAGE_KEY, next)
      } catch {
        /* Session-only, as above. */
      }
      return next
    })
  }, [])

  const value = useMemo<ThemeContextValue>(
    () => ({ preference, resolved, setPreference, toggle }),
    [preference, resolved, setPreference, toggle],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

/** Read the theme. Throws outside {@link ThemeProvider}, which is a wiring bug, not a state. */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used inside a ThemeProvider')
  return context
}
