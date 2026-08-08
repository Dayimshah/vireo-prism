import { useCallback, useMemo } from 'react'

import type { ResponseInfo } from './client'
import type { ApiPath, QueryFor, ResponseFor, RowFor } from './endpoints'
import type { ProblemError } from './problem'
import { useApi, type ApiQueryOptions } from './queries'
import { useFilters, useQueryFilters } from '@/state/filters'

/**
 * One endpoint, resolved into everything a panel needs to render itself.
 *
 * Why this exists rather than each chart calling `useApi` directly
 * ---------------------------------------------------------------
 * Fifty-odd panels each need the same six steps: narrow the window to what the path
 * accepts, merge the filters, merge any endpoint-specific arguments, gate the request until
 * the dataset bounds have arrived, decide whether a successful response was *empty*, and
 * hand the transport facts to the cache badge. Written per panel that is six lines to get
 * subtly wrong fifty times.
 *
 * It also contains **the one cast**. `useQueryFilters` returns `Partial<QueryFilters>` —
 * deliberately partial, because {@link windowParamsFor} strips the date keys an endpoint
 * rejects — and `useApi` requires `QueryFor<P>`, which for a windowed endpoint has
 * `date_from` and `date_to` as *required*. No type can bridge those: the whole point is that
 * the object's shape is decided at runtime from the path. So the assertion happens here,
 * once, next to the runtime narrowing that justifies it, instead of at every call site where
 * it would read as boilerplate nobody checks.
 *
 * Empty is not the same as failed, and neither is the same as zero
 * ---------------------------------------------------------------
 * {@link Panel.boundary} carries `isEmpty` computed from the *response*, not inferred from a
 * falsy value. A query that ran and matched nobody is a finding; rendering it as a chart with
 * no series would draw axes from nothing and read as "all values are zero", which in an API
 * where `null` means *undefined* is the exact confusion the null-handling discipline exists
 * to prevent. `QueryBoundary` says so in words instead.
 *
 * The four value endpoints — `/overview`, an experiment's results and the two `/meta`
 * routes — return one object rather than rows, and are therefore never "empty". The check is
 * `Array.isArray` on the payload rather than a table of which paths return lists, because a
 * table would be a second source of truth about the schema.
 */

/**
 * Endpoint-specific query arguments, beyond the window and the filters.
 *
 * `limit`, `min_cohort_size`, `segment_by`, `max_months`, `alpha`, `observation_end` — each
 * belongs to a handful of routes and is typed against the path, so passing `segment_by` to an
 * endpoint without it fails to compile.
 *
 * The tuple guard is the same one {@link AcceptsParam} needs: `Partial<never>` collapses to
 * `never` for the parameterless routes, and a `never`-typed optional property produces an
 * error message about `never` rather than about the argument being unwanted.
 */
type ExtraParams<P extends ApiPath> = [QueryFor<P>] extends [never]
  ? Record<string, never>
  : Partial<QueryFor<P>>

/** Options for {@link usePanel}. */
export interface PanelOptions<P extends ApiPath> {
  /** Endpoint-specific query arguments. Typed to the path. */
  extra?: ExtraParams<P>

  /**
   * Path parameters. Required only by the two `/experiments/{experiment_key}/…` routes.
   *
   * Part of the query key, so two experiments do not share one cache entry — the failure
   * that would show the first experiment's numbers under every other experiment's name.
   */
  pathParams?: Record<string, string | number>

  /**
   * Additional gate on top of the window gate.
   *
   * For a panel that cannot ask its question yet for a reason of its own — the Experiments
   * page before a test has been chosen. A page that disables a panel this way owns telling
   * the reader why: `isWaiting` reports only the window gate, so a deliberately disabled
   * panel would otherwise show a skeleton that never resolves.
   */
  enabled?: boolean

  /** Passed through to react-query. `staleTime: Infinity` for data that cannot change. */
  query?: Omit<ApiQueryOptions<P>, 'enabled'>
}

/** Everything a panel needs, resolved. */
export interface Panel<P extends ApiPath> {
  /** The parsed response body, or `undefined` before the first success. */
  payload: ResponseFor<P> | undefined

  /**
   * The rows, or `[]` before the first success.
   *
   * Always an array, so a chart can map without a guard. For the four value endpoints
   * `RowFor<P>` resolves to `never` and this is permanently empty — read {@link payload}
   * there instead, which is what the `never` is telling you.
   */
  rows: readonly RowFor<P>[]

  /** Cache state, timing and correlation id for the response on screen. */
  info: ResponseInfo | undefined

  /** True once a response has arrived and it held at least one row. */
  hasRows: boolean

  /** Spread straight into {@link ChartCard} or {@link QueryBoundary}. */
  boundary: {
    isPending: boolean
    error: ProblemError | null
    isEmpty: boolean
    isWaiting: boolean
    hasFilters: boolean
    hasLanguageFilter: boolean
    onRetry: () => void
    info: ResponseInfo | undefined
  }
}

/**
 * Fetch one endpoint for one panel.
 *
 * ```tsx
 * const dau = usePanel('/kpi/dau')
 * return (
 *   <ChartCard title="Daily active users" {...dau.boundary}>
 *     <TimeSeriesChart data={dau.rows} xKey="day" series={[{ key: 'dau', label: 'DAU' }]} />
 *   </ChartCard>
 * )
 * ```
 *
 * @param path Endpoint path without the `/api/v1` prefix.
 * @param options Endpoint-specific arguments and gating.
 */
export function usePanel<P extends ApiPath>(path: P, options: PanelOptions<P> = {}): Panel<P> {
  const { extra, pathParams, enabled = true, query } = options

  const { params, enabled: windowReady } = useQueryFilters(path)
  const { filters, activeFilterCount } = useFilters()

  // `extra` is spread last so an explicit `observation_end` or `limit` wins over anything
  // the shared params happen to carry. Nothing overlaps today; relying on that silently is
  // how it stops being true.
  const queryParams = useMemo(
    () => ({ ...params, ...extra }) as unknown as QueryFor<P>,
    // A fresh object each render would be a new query key each render. `params` is already
    // memoised by `useQueryFilters`; `extra` is expected to be a literal, so it is
    // serialised rather than compared by reference — an inline `{ limit: 20 }` is a new
    // object every render and would otherwise refetch forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [params, JSON.stringify(extra ?? null)],
  )

  const result = useApi(
    path,
    queryParams,
    { ...query, enabled: enabled && windowReady },
    pathParams,
  )

  const { payload, info, isPending, error, isSuccess, refetch } = result

  // `payload.data` is typed per path — rows for the list endpoints, an object for the four
  // value ones — and there is no generic way to read it without narrowing the conditional
  // type. The runtime check below is the narrowing, so the cast is to the loosest shape that
  // describes both rather than to the row type, which would be a claim this cannot verify.
  const data = (payload as { data?: unknown } | undefined)?.data

  const rows = useMemo(
    () => (Array.isArray(data) ? (data as readonly RowFor<P>[]) : []),
    [data],
  )

  const onRetry = useCallback(() => {
    // Discarded deliberately: `refetch` resolves with the query result, and returning that
    // promise to an `onClick` would make React warn about an unhandled rejection on a
    // failure the error panel is already reporting.
    void refetch()
  }, [refetch])

  const isEmpty = isSuccess && Array.isArray(data) && data.length === 0

  return {
    payload,
    rows,
    info,
    hasRows: rows.length > 0,
    boundary: {
      isPending,
      error,
      isEmpty,
      // Only the window gate. A caller-disabled panel is the page's to explain — see
      // {@link PanelOptions.enabled}.
      isWaiting: !windowReady,
      hasFilters: activeFilterCount > 0,
      hasLanguageFilter: filters.language.length > 0,
      onRetry,
      info,
    },
  }
}
