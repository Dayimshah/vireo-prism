import {
  keepPreviousData,
  QueryClient,
  useQuery,
  type UseQueryOptions,
  type UseQueryResult,
} from '@tanstack/react-query'

import {
  get,
  getHealth,
  type ApiPath,
  type HealthStatus,
  type QueryFor,
  type ResponseFor,
} from './endpoints'
import { ProblemError } from './problem'
import type { ApiResult, QueryParams, ResponseInfo } from './client'

/**
 * The react-query layer: one generic hook, a key factory, and the retry policy.
 *
 * One hook rather than 54
 * ----------------------
 * `endpoints.get` is already typed by path, so a `useApi('/kpi/dau', …)` inherits the
 * exact parameter and row types for that route. Writing 54 named hooks would be
 * transcription with no added guarantee — and the first one to drift from its endpoint
 * would still compile.
 *
 * The retry policy is a correctness decision, not a performance one
 * ---------------------------------------------------------------
 * react-query retries three times by default, for every error. Against this API that is
 * actively wrong in two ways:
 *
 * * **A 422 is deterministic.** A missing window or an unknown filter value returns the
 *   same 422 however many times it is sent, so three retries are three guaranteed
 *   failures — and they delay the error message a reader needs by several seconds.
 * * **The rate limiter has a 60-token bucket per client address**, and it is per-worker
 *   and in-process. A dashboard page issuing six requests turns into twenty-four under
 *   default retries, which can exhaust the bucket and convert a single real failure into
 *   a page of 429s that look like a different problem entirely.
 *
 * So retries are limited to the errors that can actually clear: 429, 503, 504 and a
 * network failure. {@link ProblemError.isRetryable} owns that judgement.
 */

/**
 * Query keys.
 *
 * `[SCOPE, path, params]`. react-query hashes these deterministically with sorted object
 * keys, so two calls with the same parameters in a different order share a cache entry —
 * which matches the API's own cache, where keys carry *resolved* parameters so
 * `?country=IN` and `?country=India` are one entry.
 *
 * The leading scope constant makes a whole-app invalidation possible without listing every
 * path, which is what the manual refresh control needs.
 */
// No `as const` needed: a `const` declaration initialised with a string literal already has
// the literal type `'prism'`, which is what the key factory below relies on.
export const QUERY_SCOPE = 'prism'

export const queryKeys = {
  /** Everything. Used by the refresh control to invalidate the app in one call. */
  all: [QUERY_SCOPE] as const,

  /** One endpoint with one set of parameters. */
  endpoint: (path: ApiPath, params?: QueryParams) =>
    // `params` is included even when undefined so a parameterless endpoint's key has the
    // same arity as everything else, and a partial-match invalidation on `[SCOPE, path]`
    // reaches it.
    [QUERY_SCOPE, path, params ?? null] as const,

  /** Service health, which is not part of the analytics scope. */
  health: [QUERY_SCOPE, 'health'] as const,
} as const

/**
 * How long a result stays fresh before react-query will refetch it.
 *
 * Five minutes, which is long for a UI and correct for this data: the warehouse is a
 * seeded snapshot that changes only when someone runs the seeder or refreshes the
 * materialized views. Refetching sooner would recompute figures that cannot have moved,
 * and every one of those requests spends a rate-limit token.
 */
const STALE_TIME_MS = 5 * 60 * 1000

/**
 * How long an unused result stays in memory.
 *
 * Longer than {@link STALE_TIME_MS} on purpose. Navigating from Retention to Monetization
 * and back should not re-issue the retention queries, and holding a few hundred rows per
 * endpoint costs far less than the round trip.
 */
const GC_TIME_MS = 30 * 60 * 1000

/**
 * The shared client.
 *
 * Constructed here rather than in `main.tsx` so the defaults live beside the reasoning for
 * them, and so a test can import the same configuration it ships with.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: STALE_TIME_MS,
        gcTime: GC_TIME_MS,

        // See the module docstring: only errors that can clear are worth resending.
        retry: (failureCount, error) => {
          if (!(error instanceof ProblemError)) return false
          if (!error.isRetryable) return false
          // Two attempts after the first. A 503 during a view refresh clears in seconds; a
          // third retry mostly just delays the error a reader is waiting for.
          return failureCount < 2
        },

        retryDelay: (failureCount, error) => {
          // Honour the limiter's own instruction rather than guessing. `Retry-After` is
          // what the API says will work, and retrying sooner is guaranteed to fail again.
          if (error instanceof ProblemError && error.retryAfterSeconds !== null) {
            // One extra second of margin: the bucket refills on a clock this client cannot
            // see precisely, and landing a millisecond early wastes the whole attempt.
            return (error.retryAfterSeconds + 1) * 1000
          }
          // Exponential with a ceiling: 1s, 2s, capped at 8s.
          return Math.min(1000 * 2 ** failureCount, 8000)
        },

        // The dataset is a static snapshot. Refetching every time a reader alt-tabs back
        // would spend rate-limit tokens to redraw identical charts.
        refetchOnWindowFocus: false,

        // Reconnecting is worth a refetch: the previous fetch may have failed while
        // offline, and this is the one moment the data might genuinely be newer.
        refetchOnReconnect: true,

        // Keep the previous result visible while a new window or filter loads. Without
        // this, every filter change blanks the page to skeletons — which reads as the app
        // restarting rather than as a chart updating.
        placeholderData: keepPreviousData,
      },
      mutations: {
        // The only mutation is the admin view refresh. Retrying it is wrong: it either
        // started a refresh or was rejected for a reason resending will not change, and a
        // duplicate `REFRESH MATERIALIZED VIEW` is expensive.
        retry: false,
      },
    },
  })
}

/**
 * What {@link useApi} returns: the query result, with the transport facts alongside.
 *
 * An intersection rather than `interface … extends`, which does not compile here:
 * `UseQueryResult` is a *discriminated union* over `status`, and an interface can only
 * extend a type with statically known members. The union is the useful part — it is what
 * lets `if (query.isSuccess)` narrow `data` to non-undefined — and an intersection
 * distributes across it, so that narrowing survives. Flattening it into an interface would
 * have cost every call site its type guard.
 */
export type ApiQueryResult<P extends ApiPath> = UseQueryResult<
  ApiResult<ResponseFor<P>>,
  ProblemError
> & {
  /** The parsed `data` payload, or `undefined` before the first success. */
  payload: ResponseFor<P> | undefined

  /** Cache state, timing and correlation id for the response currently displayed. */
  info: ResponseInfo | undefined
}

/**
 * Options accepted by {@link useApi}, beyond the query key.
 *
 * `queryKey` and `queryFn` are omitted because this hook owns both — supplying either
 * would let a call site desynchronise the key from the parameters actually sent, which is
 * the one way to make react-query serve a chart the wrong data.
 */
export type ApiQueryOptions<P extends ApiPath> = Omit<
  UseQueryOptions<ApiResult<ResponseFor<P>>, ProblemError>,
  'queryKey' | 'queryFn'
>

/**
 * Fetch one endpoint.
 *
 * ```ts
 * const dau = useApi('/kpi/dau', { date_from, date_to })
 * dau.payload?.data.forEach(...)   // typed as DauRow[]
 * ```
 *
 * @param path Endpoint path without the `/api/v1` prefix.
 * @param params Query parameters for that endpoint, typed to it.
 * @param options Extra react-query options — `enabled` is the common one, for a query
 *   that must wait on the dataset bounds before it can build a valid window.
 * @param pathParams Path parameters, required only by the two experiment routes.
 */
export function useApi<P extends ApiPath>(
  path: P,
  params?: QueryFor<P>,
  options?: ApiQueryOptions<P>,
  pathParams?: Record<string, string | number>,
): ApiQueryResult<P> {
  const result = useQuery<ApiResult<ResponseFor<P>>, ProblemError>({
    // The key carries the path params too, or the two experiment routes would share one
    // cache entry across every experiment and show the first one's numbers for all of them.
    queryKey: pathParams
      ? ([...queryKeys.endpoint(path, params as QueryParams | undefined), pathParams] as const)
      : queryKeys.endpoint(path, params as QueryParams | undefined),

    queryFn: ({ signal }) =>
      // The cast crosses from this hook's generic parameter into the conditional argument
      // tuple `get` declares. It is contained to this one call: `params` and `pathParams`
      // are already typed against `P` in this signature, so a wrong call site fails at the
      // hook rather than here.
      (
        get as (
          p: P,
          q: unknown,
          o: { signal: AbortSignal; path?: Record<string, string | number> },
        ) => Promise<ApiResult<ResponseFor<P>>>
      )(path, params, pathParams ? { signal, path: pathParams } : { signal }),

    ...options,
  })

  return {
    ...result,
    payload: result.data?.data,
    info: result.data?.info,
  }
}

/**
 * Fetch service health.
 *
 * Separate from {@link useApi} because `/health` returns a bare document rather than the
 * standard envelope, and because it is the one endpoint worth polling: it is exempt from
 * rate limiting, and it is how the shell knows to tell a reader the database is unseeded
 * rather than letting every chart fail on its own.
 *
 * Read `data.status` rather than trusting the 200 — `degraded` is a success response.
 */
export function useHealth(): UseQueryResult<ApiResult<HealthStatus>, ProblemError> {
  return useQuery<ApiResult<HealthStatus>, ProblemError>({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => getHealth({ signal }),
    // Shorter than the analytics default: this is the value that tells a reader whether an
    // empty dashboard is a data problem or a service problem, and it is the one thing that
    // legitimately changes while they watch.
    staleTime: 30 * 1000,
    refetchInterval: 60 * 1000,
    // Health is diagnostic. A stale "ok" beside a page of failures would be actively
    // misleading, so the previous value is not kept while refetching.
    placeholderData: undefined,
    retry: 1,
  })
}
