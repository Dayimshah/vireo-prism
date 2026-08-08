import { createContext, useCallback, useContext, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useApi } from '@/api/queries'
import { ignoresWindow, windowParamsFor } from '@/api/endpoints'
import type { ApiPath, DatasetBounds, FilterOptions } from '@/api/endpoints'
import {
  clampWindow,
  defaultWindow,
  isOrderedWindow,
  isValidIsoDate,
  type Bounds,
  type DateWindow,
} from '@/lib/dates'

/**
 * The reporting window and the eight filters, shared by every page.
 *
 * The URL is the state
 * -------------------
 * There is no `useState` for the window or the filters. They are read out of the query
 * string on every render and written back through `setSearchParams`, which means:
 *
 * * A configured view is a link. "Retention for Indian premium users over 90 days" can be
 *   pasted into a message, and that is the normal way these get shared.
 * * Back and forward work on filter changes, because each one is a history entry.
 * * There is no second copy to fall out of step with the first. A `useState` mirror synced
 *   to the URL by an effect is the classic source of a render loop, and of the subtler bug
 *   where a link opens showing the previous reader's filters for one frame.
 *
 * The window cannot be defaulted before the dataset bounds arrive
 * --------------------------------------------------------------
 * The API requires `date_from` and `date_to` with no defaults, deliberately, so a "last 30
 * days" default cannot open every chart empty against a dataset generated months earlier.
 * This dataset ends 2026-08-06, and today is later than that.
 *
 * So a first visit has no window at all until `/meta/bounds` responds, and
 * {@link FilterState.isReady} is `false` until it does. Every analytics query must gate on
 * it — `useApi(path, params, { enabled: filters.isReady })` — because firing without a
 * window is a guaranteed 422, and a page full of 422s on first load reads as a broken app
 * rather than as one still starting.
 */

/** The six filters with a catalogue behind them, plus the two that do not have one. */
export const FILTER_KEYS = [
  'country',
  'channel',
  'persona',
  'device',
  'genre',
  'content_type',
  'language',
] as const

export type FilterKey = (typeof FILTER_KEYS)[number]

/** The filter values currently applied. */
export interface FilterValues {
  country: string[]
  channel: string[]
  persona: string[]
  device: string[]
  genre: string[]
  content_type: string[]
  /**
   * Catalogue language.
   *
   * The one filter with **no allowlist**: the API documents that an unknown value narrows
   * the result to nothing rather than raising. So a typo here produces an empty chart with
   * no error, which is why the UI must show the applied value rather than hiding it in a
   * collapsed control.
   */
  language: string[]
  /**
   * Currently-paid users only (`true`), currently-unpaid only (`false`), or both
   * (`null`).
   *
   * Tri-state, and `null` is not the same as `false`. Dropping a `false` because it is
   * falsy would silently widen a query from unpaid users to every user — the API would
   * answer 200 and the chart would be wrong rather than absent.
   */
  is_premium: boolean | null
}

/** The window and filters as an endpoint's query parameters. */
export interface QueryFilters extends FilterValues, DateWindow {}

interface FilterState {
  /** The window, or `null` until the dataset bounds have loaded. */
  window: DateWindow | null

  /** The applied filters. Arrays are empty when a dimension is unfiltered. */
  filters: FilterValues

  /**
   * True once a valid window exists. Gate every analytics query on this.
   *
   * @see the module docstring for why firing early is a guaranteed 422.
   */
  isReady: boolean

  /** Dataset boundaries, once known. */
  bounds: DatasetBounds | null

  /**
   * The database is reachable and migrated but holds no data — run `make seed`.
   *
   * Distinct from `isLoadingMeta` and from `metaError`, and all three look the same from a
   * page's point of view: no window, so no charts. Only this one has a fix the reader can
   * carry out, so the shell must be able to tell them rather than showing an empty date
   * picker and letting them conclude the app is broken.
   */
  isUnseeded: boolean

  /** The accepted values for each dimension, once known. */
  options: FilterOptions | null

  /** True while either metadata request is in flight. */
  isLoadingMeta: boolean

  /** Set when the metadata could not be fetched — the app cannot build a window without it. */
  metaError: Error | null

  /** How many filters are narrowing the population. Shown as a badge on the filter button. */
  activeFilterCount: number

  // There is deliberately no `queryParams` here. A single pre-spread params object is
  // exactly the trap that five endpoints turn into a 422 — see {@link useQueryFilters},
  // which takes the target path and narrows the window to what that path accepts. Exposing
  // the unnarrowed pair as well would leave the wrong option one keystroke closer than the
  // right one.

  setWindow: (window: DateWindow) => void
  setFilter: (key: FilterKey, values: string[]) => void
  setIsPremium: (value: boolean | null) => void
  clearFilters: () => void
}

const FilterContext = createContext<FilterState | null>(null)

/** URL parameter names. Identical to the API's, so a URL reads like the request it makes. */
const PARAM_FROM = 'date_from'
const PARAM_TO = 'date_to'
const PARAM_PREMIUM = 'is_premium'

/**
 * Read a repeated query parameter into an array.
 *
 * Repeated keys (`?country=India&country=Brazil`) rather than a comma-joined value,
 * matching how the API parses them — so the browser URL and the request query string have
 * the same shape and one can be read off the other while debugging.
 */
function readList(params: URLSearchParams, key: string): string[] {
  return params
    .getAll(key)
    .flatMap((value) => value.split(','))
    .map((value) => value.trim())
    .filter(Boolean)
}

/** Read the tri-state premium filter. Anything unrecognised means "both". */
function readPremium(params: URLSearchParams): boolean | null {
  const raw = params.get(PARAM_PREMIUM)
  if (raw === 'true') return true
  if (raw === 'false') return false
  return null
}

/**
 * Narrow the API's bounds to a pair of real dates, or `null`.
 *
 * Both date fields are **optional and nullable** in the schema, and that is not
 * defensive typing — an **unseeded** database has no activity at all, so
 * `/meta/bounds` answers with `is_seeded: false` and both dates `null`. A seeded
 * database always has them, which is why this cannot be found by testing against one.
 *
 * The consequence is specific: with no dates there is no window that can be built, so no
 * analytics query can be issued, and the UI has to say `make seed` rather than showing a
 * date picker with nothing in it. Returning `null` here is what lets the shell distinguish
 * "still loading" from "there is nothing to load".
 */
function usableBounds(bounds: DatasetBounds | null): Bounds | null {
  if (!bounds) return null
  const { first_activity_date: first, last_activity_date: last } = bounds
  if (!isValidIsoDate(first) || !isValidIsoDate(last)) return null
  return { first_activity_date: first, last_activity_date: last }
}

export function FilterProvider({ children }: { children: React.ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams()

  // Metadata. Neither takes parameters, so neither needs a window — they are what makes
  // building one possible. `staleTime: Infinity` is honest: the bounds and the dimension
  // catalogue only change when someone reseeds, which restarts the app anyway.
  const boundsQuery = useApi('/meta/bounds', undefined, { staleTime: Infinity })
  const optionsQuery = useApi('/meta/filters', undefined, { staleTime: Infinity })

  const bounds = boundsQuery.payload?.data ?? null
  const options = optionsQuery.payload?.data ?? null

  // The date arithmetic needs two real dates; the API's bounds may carry neither. Derived
  // once here so `window` and `setWindow` cannot disagree about whether they exist.
  const dateBounds = useMemo(() => usableBounds(bounds), [bounds])

  const filters = useMemo<FilterValues>(
    () => ({
      country: readList(searchParams, 'country'),
      channel: readList(searchParams, 'channel'),
      persona: readList(searchParams, 'persona'),
      device: readList(searchParams, 'device'),
      genre: readList(searchParams, 'genre'),
      content_type: readList(searchParams, 'content_type'),
      language: readList(searchParams, 'language'),
      is_premium: readPremium(searchParams),
    }),
    [searchParams],
  )

  const window = useMemo<DateWindow | null>(() => {
    const from = searchParams.get(PARAM_FROM)
    const to = searchParams.get(PARAM_TO)

    // Both present and well-formed: honour them, clamped into the dataset. A reader can
    // edit the URL and a bookmark can outlive a reseed, so an out-of-range window is a
    // normal arrival rather than an error — `clampWindow` slides it back, preserving length.
    if (isValidIsoDate(from) && isValidIsoDate(to)) {
      const requested: DateWindow = { date_from: from, date_to: to }
      // A reversed window is returned untouched so the API rejects it with a 422 that names
      // the problem. Silently swapping the ends would show a reader data they did not ask
      // for and teach them the control works when it does not.
      if (!isOrderedWindow(requested)) return requested
      return clampWindow(requested, dateBounds)
    }

    // Nothing usable in the URL. A default is only possible once the bounds are known —
    // see the module docstring. `dateBounds` is also null for an unseeded database, which
    // is why `isUnseeded` exists: the two look identical here and need different messages.
    if (!dateBounds) return null
    return defaultWindow(dateBounds)
  }, [searchParams, dateBounds])

  const isReady = window !== null && isOrderedWindow(window)

  const activeFilterCount = useMemo(() => {
    let count = 0
    for (const key of FILTER_KEYS) {
      if (filters[key].length > 0) count += 1
    }
    if (filters.is_premium !== null) count += 1
    return count
  }, [filters])

  /**
   * Write to the URL.
   *
   * `replace` for a window change and `push` for a filter change. Adjusting a date range
   * is usually a few corrections in a row, and each one as a history entry would mean five
   * presses of Back to leave the page. A filter change is a deliberate step worth
   * returning to.
   */
  const update = useCallback(
    // Named `historyOptions` rather than `options`, which is already the filter catalogue
    // in this scope. Shadowing it here would compile and read as if this function had
    // access to the catalogue.
    (mutate: (params: URLSearchParams) => void, historyOptions: { replace?: boolean } = {}) => {
      setSearchParams(
        (current) => {
          // Cloned rather than mutated: react-router hands back the live object, and
          // editing it in place can leave the URL and the params disagreeing.
          const next = new URLSearchParams(current)
          mutate(next)
          return next
        },
        { replace: historyOptions.replace ?? false },
      )
    },
    [setSearchParams],
  )

  const setWindow = useCallback(
    (next: DateWindow) => {
      const clamped = clampWindow(next, dateBounds)
      update(
        (params) => {
          params.set(PARAM_FROM, clamped.date_from)
          params.set(PARAM_TO, clamped.date_to)
        },
        { replace: true },
      )
    },
    // `dateBounds`, not `bounds` — this closure clamps against the narrowed pair, and
    // listing the wider object would let it capture a stale null from the first render.
    [dateBounds, update],
  )

  const setFilter = useCallback(
    (key: FilterKey, values: string[]) => {
      update((params) => {
        params.delete(key)
        // An empty selection deletes the key rather than writing an empty value. `?country=`
        // would ask the API for the country named "", which matches nobody — a filtered
        // 200 with no rows, indistinguishable from a real empty result.
        for (const value of values) {
          const trimmed = value.trim()
          if (trimmed) params.append(key, trimmed)
        }
      })
    },
    [update],
  )

  const setIsPremium = useCallback(
    (value: boolean | null) => {
      update((params) => {
        if (value === null) params.delete(PARAM_PREMIUM)
        else params.set(PARAM_PREMIUM, value ? 'true' : 'false')
      })
    },
    [update],
  )

  const clearFilters = useCallback(() => {
    update((params) => {
      for (const key of FILTER_KEYS) params.delete(key)
      params.delete(PARAM_PREMIUM)
      // The window is deliberately left alone. "Clear filters" means stop narrowing the
      // population, not go back to a different reporting period — resetting the dates too
      // would throw away the reader's window without being asked.
    })
  }, [update])

  const value = useMemo<FilterState>(
    () => ({
      window,
      filters,
      isReady,
      bounds,
      // The bounds request succeeded and still yielded no usable dates. `is_seeded` is the
      // API's own flag and is checked as well as the dates, so a database holding rows the
      // API considers unseeded is reported the way the API describes it rather than the way
      // this client infers it.
      isUnseeded: bounds !== null && (!bounds.is_seeded || dateBounds === null),
      options,
      isLoadingMeta: boundsQuery.isPending || optionsQuery.isPending,
      metaError: boundsQuery.error ?? optionsQuery.error ?? null,
      activeFilterCount,
      setWindow,
      setFilter,
      setIsPremium,
      clearFilters,
    }),
    [
      window,
      filters,
      isReady,
      bounds,
      dateBounds,
      options,
      boundsQuery.isPending,
      boundsQuery.error,
      optionsQuery.isPending,
      optionsQuery.error,
      activeFilterCount,
      setWindow,
      setFilter,
      setIsPremium,
      clearFilters,
    ],
  )

  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
}

/** Read the window and filters. Throws outside {@link FilterProvider}. */
export function useFilters(): FilterState {
  const context = useContext(FilterContext)
  if (!context) throw new Error('useFilters must be used inside a FilterProvider')
  return context
}

/**
 * The window and filters for one endpoint, with a matching `enabled` flag.
 *
 * ```ts
 * const { params, enabled } = useQueryFilters('/kpi/dau')
 * const dau = useApi('/kpi/dau', { ...params }, { enabled })
 * ```
 *
 * The path is required, and that is the point
 * ------------------------------------------
 * Five endpoints do not accept the reporting window, and the API rejects an undeclared
 * parameter with a **422** rather than ignoring it. `/geo/country-ranking` takes `date_to`
 * but not `date_from`; `/churn/risk-scorecard` and `/users/rfm-segments` take neither; the
 * two experiment routes take `observation_end` instead. Three of those five are on the
 * Audience page.
 *
 * TypeScript cannot catch this on its own: excess-property checking does **not** apply
 * through a spread, and a spread is the only way this helper is ever used. So the params are
 * narrowed at runtime by {@link windowParamsFor}, and taking the path as an argument is what
 * makes that possible — there is deliberately no path-free overload to reach for.
 *
 * `enabled` follows the same split
 * -------------------------------
 * A windowed endpoint must wait for `/meta/bounds`, because firing without a window is a
 * guaranteed 422. An endpoint that takes no window has nothing to wait for, so it is enabled
 * immediately — the RFM segments load while the bounds request is still in flight rather
 * than after it.
 */
export function useQueryFilters<P extends ApiPath>(
  path: P,
): { params: Partial<QueryFilters>; enabled: boolean } {
  const { window, filters, isReady } = useFilters()

  return useMemo(() => {
    const windowless = ignoresWindow(path)
    return {
      // Filters always apply — all 49 analytics endpoints accept the same seven dimensions
      // plus `is_premium`. Only the dates vary, so only the dates are narrowed.
      params: { ...windowParamsFor(path, window), ...filters },
      enabled: windowless || isReady,
    }
  }, [path, window, filters, isReady])
}
