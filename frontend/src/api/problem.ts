import type { components } from './schema'

/**
 * RFC 7807 problem documents, and the error type the client throws.
 *
 * Every deliberate failure in this API answers with `application/problem+json` — the
 * taxonomy in `app/core/exceptions.py` renders it, and `app/main.py` converts FastAPI's
 * own parameter failures into the same shape so `?limit=0` and `?limt=5` are
 * indistinguishable in structure. That uniformity is what lets one error component render
 * every failure.
 *
 * Two responses do not follow it, and both are handled here rather than at call sites
 * ------------------------------------------------------------------------------------
 * 1. **An unmatched route** returns Starlette's own `{"detail": "Not Found"}`. Routing
 *    happens before the application's handlers are reachable, so nothing converts it.
 * 2. **A response that is not JSON at all** — nginx returning its own 502 while the API
 *    restarts, or a proxy interposing an HTML error page.
 *
 * {@link parseProblem} normalises both into a real {@link Problem}, so a component can
 * read `problem.title` without first asking whether the server was polite.
 */

/** The problem document as the API declares it in OpenAPI. */
export type Problem = components['schemas']['ProblemDetail']

/** One field-level entry inside a validation problem. */
export type ProblemFieldError = components['schemas']['ProblemFieldError']

/**
 * Stable problem-type URIs.
 *
 * Clients switch on `type` rather than parsing `detail`, which is prose and may be
 * reworded. Mirrors `PROBLEM_BASE_URI` in `app/core/exceptions.py`.
 */
export const PROBLEM_BASE_URI = 'https://prism.vireo.dev/problems'

export const ProblemType = {
  validation: `${PROBLEM_BASE_URI}/validation-error`,
  unknownDimensionValue: `${PROBLEM_BASE_URI}/unknown-dimension-value`,
  notFound: `${PROBLEM_BASE_URI}/not-found`,
  unauthorized: `${PROBLEM_BASE_URI}/unauthorized`,
  rateLimited: `${PROBLEM_BASE_URI}/rate-limit-exceeded`,
  unavailable: `${PROBLEM_BASE_URI}/service-unavailable`,
  timeout: `${PROBLEM_BASE_URI}/query-timeout`,
  internal: `${PROBLEM_BASE_URI}/internal-error`,

  /** Not from the server. Assigned by {@link ProblemError.fromNetworkFailure}. */
  network: 'urn:prism:client:network-failure',
} as const

/**
 * The error thrown for every non-2xx response, and for a request that never arrived.
 *
 * A single error type rather than one per status: the calling component's decision is
 * almost always "is this worth a retry button, and what do I tell the reader", and both
 * answers come from fields on the problem rather than from the class.
 *
 * Extends `Error` so it survives react-query's error channel, `instanceof` checks, and
 * anything that logs `.message`.
 */
export class ProblemError extends Error {
  /** The normalised problem document. */
  readonly problem: Problem

  /**
   * HTTP status, or 0 when the request never completed.
   *
   * Zero is deliberately distinguishable: a network failure has no status, and reporting
   * one — 500, say — would tell a reader the server rejected them when it never spoke.
   */
  readonly status: number

  /** Correlation id, when the server sent one. Quote it to find the matching log line. */
  readonly requestId: string | null

  /**
   * Seconds to wait, parsed from `Retry-After` on a 429.
   *
   * Only ever set for rate limiting. The limiter is per-worker and in-process, which the
   * API documents — so this is a hint about one worker, not a cluster-wide budget.
   */
  readonly retryAfterSeconds: number | null

  constructor(
    problem: Problem,
    options: { status?: number; requestId?: string | null; retryAfterSeconds?: number | null } = {},
  ) {
    // `detail` is the sentence written for a human, so it becomes the Error message and
    // any generic logger prints something useful without knowing about this class.
    super(problem.detail || problem.title || 'Request failed')
    this.name = 'ProblemError'
    this.problem = problem
    this.status = options.status ?? problem.status
    this.requestId = options.requestId ?? problem.request_id ?? null
    this.retryAfterSeconds = options.retryAfterSeconds ?? null
  }

  /** Field-level entries, empty when the failure was not a validation failure. */
  get fieldErrors(): ProblemFieldError[] {
    return this.problem.errors ?? []
  }

  /**
   * True when the caller sent something wrong: a bad window, an unknown filter value, a
   * misspelled parameter.
   *
   * Retrying an unchanged 422 gets the same 422, so this is what distinguishes "fix your
   * input" from "try again".
   */
  get isClientFault(): boolean {
    return this.status >= 400 && this.status < 500 && this.status !== 429
  }

  /**
   * True when the same request might succeed shortly.
   *
   * A 429 clears when the bucket refills; a 503 clears when the database or the
   * materialized views come back; a 504 may pass on a narrower window but is worth one
   * retry; a network failure clears when connectivity does. Nothing in the 4xx range
   * qualifies, because the request itself is what is wrong.
   */
  get isRetryable(): boolean {
    return this.status === 0 || this.status === 429 || this.status === 503 || this.status === 504
  }

  /**
   * True when the analytics views exist but hold no data.
   *
   * Distinct from a broken database, and the fix is different: this one is `make seed`,
   * which is worth saying to a reader looking at an empty dashboard rather than leaving
   * them to conclude the code is broken.
   */
  get isUnseeded(): boolean {
    return this.status === 503 && /seed|populat|materialized/i.test(this.problem.detail)
  }

  /**
   * Build a problem for a request that never reached the server.
   *
   * The API is on a different origin, so this covers three cases a browser reports
   * identically: the API is down, the network is down, and CORS rejected the response.
   * The message names all three, because from here they are genuinely indistinguishable
   * — `fetch` rejects with an opaque `TypeError` in every one of them, by design, so that
   * a page cannot probe another origin's reachability.
   */
  static fromNetworkFailure(cause: unknown, url: string): ProblemError {
    const reason = cause instanceof Error ? cause.message : String(cause)
    return new ProblemError(
      {
        type: ProblemType.network,
        title: 'Waking up the server...',
        status: 0,
        detail:
          `The backend is hosted on a free tier and sleeps after inactivity. ` +
          `It typically wakes up in 30–60 seconds. Please wait and the data will load automatically. ` +
          `— Dayim Shah`,
        instance: url,
      },
      { status: 0 },
    )
  }
}

/** Narrow an unknown value to a problem document without trusting the server's shape. */
function looksLikeProblem(value: unknown): value is Problem {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  // `title` and `status` are the two fields every branch of the taxonomy sets. `type` is
  // not checked, so a future problem type still parses as one.
  return typeof candidate.title === 'string' && typeof candidate.status === 'number'
}

/**
 * Turn a failed response into a {@link Problem}, whatever the server actually sent.
 *
 * Reads the body as text first and parses it here, rather than calling `response.json()`.
 * A non-JSON error body — nginx's HTML 502, a proxy's plain-text timeout — would make
 * `.json()` throw, and that exception would replace a describable failure with an opaque
 * parse error at the exact moment a reader most needs to know what happened. Text always
 * succeeds, and the raw body becomes the `detail` when it is short enough to be useful.
 */
export async function parseProblem(response: Response, url: string): Promise<Problem> {
  let raw = ''
  try {
    raw = await response.text()
  } catch {
    // Body already consumed, or the connection dropped mid-read. The status line is
    // still worth reporting.
    raw = ''
  }

  let parsed: unknown = undefined
  if (raw) {
    try {
      parsed = JSON.parse(raw)
    } catch {
      parsed = undefined
    }
  }

  if (looksLikeProblem(parsed)) {
    // Trust it, but fill anything the server left out so consumers need no fallbacks.
    return {
      ...parsed,
      instance: parsed.instance || url,
      detail: parsed.detail || parsed.title,
    }
  }

  // Starlette's unmatched-route body: `{"detail": "Not Found"}`. Recognised specifically
  // because it is the one non-problem response the API produces in normal operation.
  const detailOnly =
    typeof parsed === 'object' &&
    parsed !== null &&
    typeof (parsed as Record<string, unknown>).detail === 'string'
      ? ((parsed as Record<string, unknown>).detail as string)
      : null

  return {
    type: statusToProblemType(response.status),
    title: statusToTitle(response.status),
    status: response.status,
    detail: detailOnly ?? truncateBody(raw) ?? `The server returned ${response.status}.`,
    instance: url,
  }
}

/** Best-effort `type` for a response that carried no problem document. */
function statusToProblemType(status: number): string {
  switch (status) {
    case 401:
    case 403:
      return ProblemType.unauthorized
    case 404:
      return ProblemType.notFound
    case 422:
      return ProblemType.validation
    case 429:
      return ProblemType.rateLimited
    case 503:
      return ProblemType.unavailable
    case 504:
      return ProblemType.timeout
    default:
      return ProblemType.internal
  }
}

/** Human title for a response that carried no problem document. */
function statusToTitle(status: number): string {
  switch (status) {
    case 401:
      return 'Unauthorized'
    case 403:
      return 'Forbidden'
    case 404:
      return 'Not found'
    case 422:
      return 'Validation error'
    case 429:
      return 'Rate limit exceeded'
    case 502:
      return 'Bad gateway'
    case 503:
      return 'Service unavailable'
    case 504:
      return 'Query timed out'
    default:
      return status >= 500 ? 'Server error' : 'Request failed'
  }
}

/**
 * Use a raw non-JSON body as `detail`, if it is short enough to read.
 *
 * An HTML error page is thousands of characters of markup and would fill the UI with
 * nothing a reader can act on, so anything long is discarded in favour of the generic
 * status message. HTML is rejected outright regardless of length.
 */
function truncateBody(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  if (trimmed.startsWith('<')) return null
  if (trimmed.length > 300) return null
  return trimmed
}

/**
 * Parse `Retry-After` into seconds.
 *
 * RFC 9110 permits either a delay in seconds or an HTTP date; this API's limiter sends
 * seconds. The date form is handled anyway because it costs three lines and a proxy
 * between here and the API is free to rewrite the header.
 */
export function parseRetryAfter(header: string | null): number | null {
  if (!header) return null

  const seconds = Number(header)
  if (Number.isFinite(seconds) && seconds >= 0) return seconds

  const timestamp = Date.parse(header)
  if (Number.isNaN(timestamp)) return null

  // Never negative: a clock skew that puts the date in the past should read as "retry
  // now", not as a negative countdown.
  return Math.max(0, Math.round((timestamp - Date.now()) / 1000))
}
