import { API_BASE_URL } from './config'
import { parseProblem, parseRetryAfter, ProblemError } from './problem'

/**
 * The fetch wrapper every request goes through.
 *
 * Three things happen here that must not be duplicated at call sites: query strings are
 * built to the exact shape the API's parameter validation expects, response headers are
 * lifted into the returned value, and every failure becomes a
 * {@link ProblemError}.
 *
 * Why query-string construction is not `URLSearchParams(params)`
 * --------------------------------------------------------------
 * Passing an object straight to `URLSearchParams` stringifies each value, and two of this
 * API's conventions break under that:
 *
 * * **Array filters must repeat the key.** `country=India&country=Brazil` is what
 *   FastAPI parses into `list[str]`. `URLSearchParams` would render the array as
 *   `country=India,Brazil` — a single value, which the API then rejects as an unknown
 *   country whose name contains a comma. The failure is at least loud.
 * * **An absent filter must be absent.** `undefined` stringifies to the literal
 *   `"undefined"`, and `null` to `"null"` — both of which arrive as real filter values
 *   and 422 as unknown. An empty string is worse than either: for `date_from` it reads as
 *   a malformed date, and for a filter it is a value that matches nobody, which the API
 *   would honour by returning zero rows. That last one is the dangerous direction —
 *   a silently empty chart rather than an error.
 *
 * {@link buildQuery} therefore omits absent values entirely and repeats array keys.
 *
 * Why unknown parameters cannot be sent
 * -------------------------------------
 * The API applies a `strict_query` dependency globally: any query parameter the matched
 * route did not declare is a 422 listing what the endpoint does accept. So this client
 * cannot add a cache-buster, a client version, or a tracing parameter to a request — it
 * would fail every endpoint. Cache-busting is done with the `Cache-Control` header
 * instead.
 */

/** A value that may appear in a query string. */
type QueryValue = string | number | boolean | readonly (string | number)[] | null | undefined

/**
 * Narrow a query value to an array.
 *
 * `Array.isArray` cannot do this job here. Its built-in signature narrows to `any[]`, and
 * a `readonly` array is not assignable to a mutable one — so TypeScript keeps the readonly
 * form in the union, and the string branch further down fails to compile because
 * `.trim()` does not exist on an array. A declared predicate states what the built-in
 * check actually established.
 */
function isValueArray(value: QueryValue): value is readonly (string | number)[] {
  return Array.isArray(value)
}

/** Query parameters as an endpoint function supplies them. */
export type QueryParams = Record<string, QueryValue>

/**
 * Build a query string, omitting absent values and repeating array keys.
 *
 * Returns `""` (not `"?"`) when nothing survives, so it can be concatenated onto a path
 * unconditionally — a bare `?` is harmless but appears in the correlation logs and in the
 * cache key, making two identical requests look different.
 */
export function buildQuery(params: QueryParams | undefined): string {
  if (!params) return ''

  const search = new URLSearchParams()

  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue

    if (isValueArray(value)) {
      // An empty array means "no filter on this dimension", which is the same request as
      // omitting it. Sending `country=` would instead ask for the country named "", and
      // the API has no such value.
      for (const item of value) {
        if (item === null || item === undefined) continue
        const trimmed = String(item).trim()
        if (!trimmed) continue
        search.append(key, trimmed)
      }
      continue
    }

    if (typeof value === 'boolean') {
      // `is_premium` is a tri-state upstream: true, false, or absent for both. A `false`
      // must survive as the string "false" rather than being dropped as falsy — dropping
      // it would silently widen the query from unpaid users to all users.
      search.append(key, value ? 'true' : 'false')
      continue
    }

    if (typeof value === 'number') {
      // NaN would stringify to "NaN" and 422. It can only arrive from arithmetic on a
      // null figure, which is a bug worth not forwarding.
      if (!Number.isFinite(value)) continue
      search.append(key, String(value))
      continue
    }

    const trimmed = value.trim()
    if (!trimmed) continue
    search.append(key, trimmed)
  }

  const query = search.toString()
  return query ? `?${query}` : ''
}

/** Cache state for one response, read from the `X-Cache` header. */
export type CacheState = 'HIT' | 'MISS' | 'PARTIAL' | 'NONE' | 'UNKNOWN'

/**
 * Narrow the `X-Cache` header to a known state.
 *
 * `UNKNOWN` is returned when the header is missing, which is a real and specific
 * situation rather than a defensive branch: the header is cross-origin, so JavaScript can
 * only read it because the API lists it in CORS `expose_headers`. If that is ever removed,
 * every response reads `UNKNOWN` — which is honest, and visibly different from the `NONE`
 * that a genuinely non-caching endpoint reports.
 */
function parseCacheState(header: string | null): CacheState {
  switch (header) {
    case 'HIT':
    case 'MISS':
    case 'PARTIAL':
    case 'NONE':
      return header
    default:
      return 'UNKNOWN'
  }
}

/** Transport-level facts about one response, taken from its headers. */
export interface ResponseInfo {
  /** Whether the figures came from cache. */
  cache: CacheState

  /** Correlation id, matching `meta.request_id` in the body. */
  requestId: string | null

  /**
   * Server-side duration in milliseconds, from `X-Response-Time-Ms`.
   *
   * The API's own measurement, so it excludes network time. A slow chart with a fast
   * `serverMs` is a transfer or render problem, not a query problem — which is the
   * distinction this exists to make.
   */
  serverMs: number | null
}

/** A successful response: the parsed body, plus what its headers said. */
export interface ApiResult<T> {
  data: T
  info: ResponseInfo
}

/** Options for one request. */
export interface RequestOptions {
  /** Query parameters. Absent values are omitted; arrays repeat their key. */
  params?: QueryParams

  /**
   * Cancellation signal.
   *
   * react-query supplies one per query and aborts it when the component unmounts or the
   * key changes. Passing it through is what stops a filter changed three times in a
   * second from leaving two obsolete requests in flight, each of which would occupy a
   * slot in the rate limiter's bucket.
   */
  signal?: AbortSignal

  /** HTTP method. Only `/admin/refresh-analytics` is not a GET. */
  method?: 'GET' | 'POST'

  /**
   * Admin API key, sent as `X-API-Key`.
   *
   * Only the admin route requires it. Never stored by this module: it is passed in per
   * call from the form that collected it, so it does not end up in `localStorage` where
   * any script on the page could read it.
   */
  apiKey?: string

  /**
   * Bypass the API's cache for this request.
   *
   * Sends `Cache-Control: no-cache`, which the API honours by recomputing. Used by the
   * manual refresh control. Deliberately not a query parameter — `strict_query` would
   * reject an undeclared one on every endpoint.
   */
  noCache?: boolean
}

/**
 * Issue one request and return its parsed body with header metadata.
 *
 * @throws {ProblemError} On any non-2xx response, on a body that is not JSON, and on a
 *   request that never reached the server. Never returns a partial or defaulted result:
 *   an analytics figure that silently falls back to zero is worse than a visible error.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const { params, signal, method = 'GET', apiKey, noCache = false } = options

  const url = `${API_BASE_URL}${path}${buildQuery(params)}`

  const headers: Record<string, string> = {
    Accept: 'application/json, application/problem+json',
  }
  if (apiKey) headers['X-API-Key'] = apiKey
  if (noCache) headers['Cache-Control'] = 'no-cache'

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      signal,
      // The API is read-only and authenticates the one write with a header, so no cookie
      // is ever needed. `omit` matches the API's `allow_credentials=False`: sending
      // credentials against a wildcard-free CORS policy that does not allow them causes
      // the browser to reject the response, which surfaces as an opaque network failure.
      credentials: 'omit',
      // Correlation ids and cache state come from the API, not from an intermediary's
      // stale copy. The API's own cache is upstream of this and is what makes repeat
      // requests cheap.
      cache: 'no-store',
      mode: 'cors',
      redirect: 'follow',
    })
  } catch (cause) {
    // An abort is not a failure — react-query cancelled it, and the component that asked
    // is gone. Rethrowing the original `AbortError` lets react-query recognise it;
    // wrapping it in a ProblemError would surface a cancelled request as a network
    // outage in the UI.
    if (signal?.aborted || (cause instanceof DOMException && cause.name === 'AbortError')) {
      throw cause
    }
    throw ProblemError.fromNetworkFailure(cause, url)
  }

  const info: ResponseInfo = {
    cache: parseCacheState(response.headers.get('X-Cache')),
    requestId: response.headers.get('X-Request-ID'),
    serverMs: parseServerMs(response.headers.get('X-Response-Time-Ms')),
  }

  if (!response.ok) {
    const problem = await parseProblem(response, url)
    throw new ProblemError(problem, {
      status: response.status,
      // Prefer the header: on a 500 the body's fixed message may omit the id, and the
      // header is set by middleware for every response including the ones the
      // application never handled.
      requestId: info.requestId ?? problem.request_id ?? null,
      retryAfterSeconds: parseRetryAfter(response.headers.get('Retry-After')),
    })
  }

  // 204 has no body. No endpoint returns one today — the admin refresh returns a
  // document — and parsing an empty string would throw a syntax error that reads as a
  // server fault rather than an empty success.
  if (response.status === 204) {
    return { data: undefined as T, info }
  }

  try {
    const data = (await response.json()) as T
    return { data, info }
  } catch {
    // A 200 whose body is not JSON. Rare and real: a proxy that rewrites the body while
    // preserving the status. The parse error itself is discarded — a JSON syntax offset
    // tells a reader nothing they can act on, and the correlation id does.
    throw new ProblemError(
      {
        type: 'urn:prism:client:malformed-response',
        title: 'Malformed response',
        status: response.status,
        detail: `The API returned ${response.status} with a body that is not valid JSON.`,
        instance: url,
        request_id: info.requestId,
      },
      { status: response.status, requestId: info.requestId },
    )
  }
}

/** Parse `X-Response-Time-Ms`, which the API sends as a decimal string such as `38.2`. */
function parseServerMs(header: string | null): number | null {
  if (!header) return null
  const value = Number(header)
  return Number.isFinite(value) ? value : null
}
