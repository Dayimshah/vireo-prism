import { request, type ApiResult, type QueryParams, type RequestOptions } from './client'
import type { components, paths } from './schema'

/**
 * The typed endpoint surface: all 54 routes, derived from the generated schema.
 *
 * Nothing here is transcribed. Every parameter name, every optional marker and every
 * response row type is read out of `schema.d.ts` with conditional types, so regenerating
 * the schema after a server change immediately re-types every call site — and a route
 * that changed shape fails `tsc` rather than failing at runtime. Hand-written signatures
 * for 54 endpoints would have exactly one guarantee: that they were correct on the day
 * they were written.
 *
 * How a call site looks
 * ---------------------
 * ```ts
 * const { data, info } = await get('/kpi/dau', { date_from: '2026-05-01', date_to: '2026-07-31' })
 * //      ^ DataResponse_DauRow_          ^ autocompletes to this endpoint's real params
 * ```
 *
 * Paths are written without the `/api/v1` prefix, because {@link API_BASE_URL} already
 * carries it. {@link ApiPath} strips the prefix from the generated keys at the type level
 * and {@link toFullPath} adds it back at runtime, so the two cannot disagree.
 */

/**
 * The API prefix, which `API_BASE_URL` already includes.
 *
 * Kept as a literal type as well as a value: the type-level strip below needs it as a
 * template-literal pattern, and deriving both from one declaration means a change to the
 * prefix cannot update one and miss the other.
 */
const API_PREFIX = '/api/v1'
type ApiPrefix = typeof API_PREFIX

/**
 * Every path the schema declares, prefix included.
 *
 * `Extract<_, string>` rather than `& string`. The two mean the same thing here — every key
 * `openapi-typescript` emits is a string literal — but an intersection with a union of
 * literals is a no-op the compiler discards, and `no-redundant-type-constituents` flags it.
 * The narrowing is kept rather than dropped because the template-literal types below
 * (`StripPrefix`, `WithPrefix`) require string keys and would fail obscurely on a numeric one.
 */
type SchemaPath = Extract<keyof paths, string>

/**
 * Paths that support GET.
 *
 * `openapi-typescript` emits `get?: never` for a path with no GET, so testing against
 * `{ get: object }` keeps exactly the routes with a real operation — which is why
 * `/admin/refresh-analytics` is absent here and has its own function below.
 */
type SchemaGetPath = { [K in SchemaPath]: paths[K] extends { get: object } ? K : never }[SchemaPath]

/** Drop the `/api/v1` prefix from a schema path. */
type StripPrefix<P> = P extends `${ApiPrefix}${infer Rest}` ? Rest : P

/** Add the `/api/v1` prefix back, narrowed to a real schema path. */
type WithPrefix<P extends string> = `${ApiPrefix}${P}` & SchemaPath

/**
 * Every GET endpoint, written without the prefix.
 *
 * This is the union a caller passes to {@link get}, and what autocomplete offers.
 */
export type ApiPath = StripPrefix<SchemaGetPath>

/** The operation object for one GET endpoint. */
type GetOperation<P extends ApiPath> =
  paths[WithPrefix<P>] extends { get: infer Operation } ? Operation : never

/**
 * The query parameters one endpoint accepts.
 *
 * `Exclude<_, undefined>` is load-bearing: the schema marks a parameterless endpoint as
 * `query?: never`, and inferring through an optional property yields `never | undefined`.
 * Without the exclude, `/meta/filters` would take `undefined` as a valid params object and
 * every other endpoint's params would silently accept `undefined` too — losing the
 * required-window guarantee that is the main reason for typing these at all.
 */
export type QueryFor<P extends ApiPath> =
  GetOperation<P> extends { parameters: { query?: infer Q } } ? Exclude<Q, undefined> : never

/** The path parameters one endpoint requires. Only the experiment routes have any. */
export type PathParamsFor<P extends ApiPath> =
  GetOperation<P> extends { parameters: { path?: infer Pp } } ? Exclude<Pp, undefined> : never

/** The parsed 200 body for one endpoint. */
export type ResponseFor<P extends ApiPath> =
  GetOperation<P> extends { responses: { 200: { content: { 'application/json': infer R } } } }
    ? R
    : never

/**
 * The row type for a list endpoint.
 *
 * Resolves to `never` for the four endpoints that return a single object rather than rows
 * — the overview, an experiment's results, and the two `/meta` routes — so a component
 * that tries to map over one of those fails to compile instead of iterating an object.
 */
export type RowFor<P extends ApiPath> =
  ResponseFor<P> extends { data: (infer Row)[] } ? Row : never

/** The `data` payload for one endpoint, list or single value. */
export type DataFor<P extends ApiPath> = ResponseFor<P> extends { data: infer D } ? D : never

/** True when an endpoint takes no query parameters at all. */
type HasNoQuery<P extends ApiPath> = [QueryFor<P>] extends [never] ? true : false

/** True when an endpoint has no required query parameter. */
type QueryIsOptional<P extends ApiPath> =
  Record<string, never> extends QueryFor<P> ? true : false

/** True when an endpoint's path carries a parameter, as the two experiment routes do. */
type HasPathParams<P extends ApiPath> = [PathParamsFor<P>] extends [never] ? false : true

/** True when an endpoint has at least one required query parameter. */
type QueryIsRequired<P extends ApiPath> =
  HasNoQuery<P> extends true ? false : QueryIsOptional<P> extends true ? false : true

/**
 * What may be passed in the params slot.
 *
 * `undefined` for a parameterless endpoint, so `get('/meta/bounds')` needs no argument;
 * `QueryFor<P> | undefined` where every parameter is optional, so the slot can be skipped
 * *or* explicitly passed as `undefined` when a later argument is needed.
 */
type ParamsArg<P extends ApiPath> =
  HasNoQuery<P> extends true
    ? undefined
    : QueryIsOptional<P> extends true
      ? QueryFor<P> | undefined
      : QueryFor<P>

/**
 * Arguments for {@link get}, arranged so nothing required can be forgotten.
 *
 * The tuple is conditional rather than two optional parameters, because three different
 * things are required on three different sets of routes and a lone `params?:` would have
 * allowed all three mistakes:
 *
 * * `/kpi/dau` requires `date_from` and `date_to` — omitting the params object must not
 *   compile, since the API answers a missing window with a 422 that reaches a reader as a
 *   broken chart.
 * * `/meta/bounds` takes nothing and must not demand an empty object.
 * * **The two `/experiments/{experiment_key}/…` routes require the options object**, which
 *   is where the path parameter lives. This is the case the first version of this type got
 *   wrong: `options` was optional everywhere, so a call with no experiment key compiled
 *   cleanly and failed at runtime against a URL still containing the literal
 *   `%7Bexperiment_key%7D`. The probe's `@ts-expect-error` on that line is what caught it.
 */
type GetArgs<P extends ApiPath> =
  HasPathParams<P> extends true
    ? [params: ParamsArg<P>, options: GetOptions<P>]
    : QueryIsRequired<P> extends true
      ? [params: ParamsArg<P>, options?: GetOptions<P>]
      : [params?: ParamsArg<P>, options?: GetOptions<P>]

/** Per-request options, plus path parameters where the route has them. */
type GetOptions<P extends ApiPath> = Omit<RequestOptions, 'params' | 'method'> &
  ([PathParamsFor<P>] extends [never] ? { path?: undefined } : { path: PathParamsFor<P> })

/**
 * Substitute `{name}` placeholders in a path template.
 *
 * Values are percent-encoded. An experiment key is a slug in this dataset, but it reaches
 * this function from a URL segment a reader can edit, and an unencoded `/` or `?` would
 * silently change which route the request matches rather than 404ing on a key that does
 * not exist.
 */
function interpolate(path: string, params: Record<string, string | number> | undefined): string {
  if (!params) return path
  return path.replace(/\{(\w+)\}/g, (whole, key: string) => {
    const value = params[key]
    // Leaving the placeholder in place is deliberate. The request then 404s against a
    // literal `%7Bexperiment_key%7D`, which names the bug in the network log — whereas
    // substituting an empty string would produce a valid-looking URL for a different route.
    return value === undefined ? whole : encodeURIComponent(String(value))
  })
}

/** Prepend the API prefix to a caller-supplied short path. */
function toFullPath(path: ApiPath): string {
  return `${API_PREFIX}${path}`
}

/**
 * Issue a GET against any of the 53 read endpoints.
 *
 * @param path Endpoint path without the `/api/v1` prefix.
 * @returns The parsed body and the transport facts from its headers.
 * @throws {ProblemError} On any non-2xx response or unreachable API.
 */
export async function get<P extends ApiPath>(
  path: P,
  ...args: GetArgs<P>
): Promise<ApiResult<ResponseFor<P>>> {
  const [params, options] = args
  const { path: pathParams, ...requestOptions } = options ?? {}

  const resolved = interpolate(
    toFullPath(path),
    pathParams as Record<string, string | number> | undefined,
  )

  return request<ResponseFor<P>>(resolved, {
    ...requestOptions,
    // The generated parameter types are precise unions; `request` takes the general
    // query-value shape. The cast crosses that boundary once, here, rather than at 53
    // call sites — and it is safe in the direction that matters: anything the schema
    // declares is a string, number, boolean, array or null, which is exactly what
    // `buildQuery` handles.
    params: params as QueryParams | undefined,
  })
}

// ---------------------------------------------------------------------------
// The routes whose shape differs enough to be worth naming
// ---------------------------------------------------------------------------

/** Service health, as reported by `/health`. */
export type HealthStatus = components['schemas']['HealthStatus']

/** The filter catalogue: every accepted value for every dimension. */
export type FilterOptions = components['schemas']['FilterOptions']

/** First and last activity dates in the dataset, and whether it has been seeded. */
export type DatasetBounds = components['schemas']['DatasetBounds']

/** The overview tiles with period-over-period deltas. */
export type OverviewSchema = components['schemas']['OverviewSchema']

/** One overview tile. */
export type Tile = components['schemas']['TileSchema']

/** An experiment's significance results. */
export type ExperimentResults = components['schemas']['ExperimentResultsSchema']

/** The outcome of a materialized-view refresh. */
export type RefreshResult = components['schemas']['RefreshResult']

/**
 * Fetch service health.
 *
 * Returned bare rather than in the standard envelope — it is the one endpoint a container
 * healthcheck calls, and unwrapping `{data, meta}` to read a status would be a poor
 * contract for a shell script.
 *
 * Note the status codes: `degraded` returns **200**, not an error. A migrated but unseeded
 * database is a working container that needs `make seed`, and only `error` gives 503. So a
 * caller must read `data.status` rather than treating a 200 as healthy.
 */
export async function getHealth(options: RequestOptions = {}): Promise<ApiResult<HealthStatus>> {
  return request<HealthStatus>(`${API_PREFIX}/health`, options)
}

/**
 * Trigger a materialized-view refresh. Requires the admin key.
 *
 * @param apiKey Sent as `X-API-Key`. Never stored — see {@link RequestOptions.apiKey}.
 * @param concurrent Leave `true` unless this is the first refresh after a migration:
 *   `REFRESH MATERIALIZED VIEW CONCURRENTLY` requires a populated view and fails on an
 *   empty one, which is why the API exposes the choice rather than deciding for you.
 */
export async function refreshAnalytics(
  apiKey: string,
  concurrent = true,
  options: Omit<RequestOptions, 'apiKey' | 'method' | 'params'> = {},
): Promise<ApiResult<{ data: RefreshResult; meta: components['schemas']['ResponseMeta'] }>> {
  return request(`${API_PREFIX}/admin/refresh-analytics`, {
    ...options,
    method: 'POST',
    apiKey,
    params: { concurrent },
  })
}

/**
 * The two `segment_by` allowlists, which are **not** the same set.
 *
 * Retention segments by `device`; the funnel segments by `form_factor` and `platform`
 * instead. This is not an oversight in the API — the two queries expose different
 * dimensions — so they are declared as separate types and a shared union would let a
 * component send a value one of the endpoints rejects with a 422.
 */
export type RetentionSegmentBy = NonNullable<QueryFor<'/retention/by-segment'>['segment_by']>
export type FunnelSegmentBy = NonNullable<QueryFor<'/funnel/by-segment'>['segment_by']>

/**
 * The reporting window is **not** accepted by every endpoint, and sending it where it is not
 * accepted is a 422.
 *
 * The API rejects any undeclared query parameter rather than ignoring it — `strict_query`
 * in `app/middleware.py`, on the grounds that `?contry=India` returning the whole dataset
 * is worse than an error. Correct, and it means a client cannot spread one params object
 * into every request.
 *
 * Verified against the running API, not inferred:
 *
 * | Shape                            | Count | Notes                                      |
 * |----------------------------------|-------|--------------------------------------------|
 * | `date_from` + `date_to`          | 44    | every KPI, cohort, funnel, content route   |
 * | `date_to` only                   | 1     | `/geo/country-ranking`                     |
 * | neither                          | 2     | `/churn/risk-scorecard`, `/users/rfm-segments` |
 * | neither, but `observation_end`   | 2     | the experiment routes                      |
 *
 * `curl '/geo/country-ranking?date_from=…'` answers
 * `422 Unknown query parameter(s): date_from`. The three exceptions are all *point-in-time*
 * questions — who is at risk now, which countries rank now, what RFM segment someone is in
 * now — so a window is genuinely meaningless for them rather than merely omitted.
 *
 * Why this needs a runtime guard and not just types
 * ------------------------------------------------
 * TypeScript's excess-property check does **not** apply through a spread. Measured:
 *
 * ```ts
 * const p = { date_from: '…', date_to: '…' }
 * const bad: QueryFor<'/geo/country-ranking'> = { ...p }        // compiles
 * const alsoBad: QueryFor<'/geo/country-ranking'> = { date_from: '…', date_to: '…' }  // errors
 * ```
 *
 * Since the entire purpose of a shared filter object is to be spread, the types alone would
 * have let all three of these through to a runtime 422 — on the Audience page, which owns
 * two of them. So {@link windowParamsFor} strips the keys the target does not accept.
 */

/** True when an endpoint accepts a given query parameter. */
type AcceptsParam<P extends ApiPath, K extends string> = [QueryFor<P>] extends [never]
  ? // `keyof never` widens to `string | number | symbol`, which would report *every* key as
    // accepted for the five parameterless endpoints. The tuple wrapper detects `never`
    // without distributing over the union, which is what makes this sound.
    false
  : K extends keyof QueryFor<P>
    ? true
    : false

/** Every path that does not accept `date_from`. Computed from the schema. */
type PathsWithoutDateFrom = {
  [P in ApiPath]: AcceptsParam<P, 'date_from'> extends true ? never : P
}[ApiPath]

/** Every path that does not accept `date_to`. Computed from the schema. */
type PathsWithoutDateTo = {
  [P in ApiPath]: AcceptsParam<P, 'date_to'> extends true ? never : P
}[ApiPath]

/**
 * Paths to omit `date_from` from.
 *
 * Deliberately **not** annotated as `readonly PathsWithoutDateFrom[]`. An annotation would
 * widen the literals to that union, and `(typeof NO_DATE_FROM)[number]` would then resolve
 * back to the union itself — making the completeness check below compare a type to itself
 * and pass no matter what is missing. `as const` keeps the literals, and the two assertions
 * in {@link _tableChecks} check the two directions separately.
 */
const NO_DATE_FROM = [
  '/geo/country-ranking',
  '/churn/risk-scorecard',
  '/users/rfm-segments',
  '/experiments/{experiment_key}/variants',
  '/experiments/{experiment_key}/results',
  // The parameterless routes and `/search`. Listed because the completeness check below
  // cannot pass without them, not because a caller would spread a window into them — and a
  // table that is only partly true is one nobody can rely on.
  '/search',
  '/meta/filters',
  '/meta/bounds',
  '/health',
  // The experiment catalogue. Parameterless like `/meta/*`: it reads definitions from
  // `core.experiments`, so it accepts neither a window nor the filters. Adding the route
  // server-side made the completeness assertion below fail until it was listed here,
  // which is precisely what that assertion is for.
  '/experiments',
] as const

/** Paths to omit `date_to` from. `as const` for the same reason as above. */
const NO_DATE_TO = [
  '/churn/risk-scorecard',
  '/users/rfm-segments',
  '/experiments/{experiment_key}/variants',
  '/experiments/{experiment_key}/results',
  '/search',
  '/meta/filters',
  '/meta/bounds',
  '/health',
  '/experiments',
] as const

/**
 * Both tables checked against the schema, in both directions.
 *
 * The two directions catch different mistakes, and only one of them is loud:
 *
 * * **Complete** — every path the schema says lacks the parameter is in the table. A gap here
 *   means the window is sent to an endpoint that rejects it: a 422, visible immediately.
 * * **Sound** — every path in the table genuinely lacks the parameter. A wrong entry here
 *   strips a parameter the endpoint *does* accept, so the request succeeds and returns
 *   figures for the wrong period. No error, just wrong numbers — the failure this whole
 *   module is built to prevent.
 *
 * A single assignment could not check both: annotating the array with the derived union
 * would make `(typeof TABLE)[number]` resolve back to that union and compare it to itself.
 * Hence `as const` above and four separate conditionals here. When the API changes, the
 * failing line names the path.
 */
const _tableChecks: [
  PathsWithoutDateFrom extends (typeof NO_DATE_FROM)[number] ? true : never,
  (typeof NO_DATE_FROM)[number] extends PathsWithoutDateFrom ? true : never,
  PathsWithoutDateTo extends (typeof NO_DATE_TO)[number] ? true : never,
  (typeof NO_DATE_TO)[number] extends PathsWithoutDateTo ? true : never,
] = [true, true, true, true]
void _tableChecks

const NO_DATE_FROM_SET: ReadonlySet<string> = new Set(NO_DATE_FROM)
const NO_DATE_TO_SET: ReadonlySet<string> = new Set(NO_DATE_TO)

/**
 * Narrow a reporting window to the keys one endpoint actually accepts.
 *
 * Returns a fresh object holding only the legal keys, so the result can be spread into a
 * params object without carrying a 422 with it.
 *
 * ```ts
 * const params = { ...windowParamsFor('/geo/country-ranking', window), ...filters }
 * // -> { date_to: '2026-08-06', country: [...] }   // no date_from
 * ```
 */
export function windowParamsFor(
  path: ApiPath,
  window: { date_from: string; date_to: string } | null,
): Partial<{ date_from: string; date_to: string }> {
  if (!window) return {}

  const params: Partial<{ date_from: string; date_to: string }> = {}
  if (!NO_DATE_FROM_SET.has(path)) params.date_from = window.date_from
  if (!NO_DATE_TO_SET.has(path)) params.date_to = window.date_to
  return params
}

/**
 * True when an endpoint takes no reporting window at all.
 *
 * A page uses this to decide whether to tell the reader that a panel ignores the window —
 * an RFM segment count that does not move when the dates change is otherwise read as a bug.
 */
export function ignoresWindow(path: ApiPath): boolean {
  return NO_DATE_FROM_SET.has(path) && NO_DATE_TO_SET.has(path)
}
