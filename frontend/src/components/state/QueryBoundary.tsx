import { AlertTriangle, Clock, Database, Inbox, PlugZap, RefreshCw, SearchX } from 'lucide-react'
import type { ReactNode } from 'react'

import { IS_DEV } from '@/api/config'
// Type-only here: this module reads fields off a `ProblemError` but never constructs one or
// tests with `instanceof`, so the class must not survive into the bundle.
import type { ProblemError } from '@/api/problem'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/**
 * The four states every data view can be in, rendered consistently.
 *
 * Loading, failed, empty and ready are genuinely four states, not three
 * ---------------------------------------------------------------------
 * The one most often collapsed is **empty**, and collapsing it is what produces the two
 * worst readings of this dataset:
 *
 * * An empty result treated as an error tells a reader something is broken when the honest
 *   answer is "no users matched that filter" — which is a finding, not a fault.
 * * An empty result treated as ready renders a chart with no series and axes drawn from
 *   nothing, which looks like data whose values are all zero. In an API where `null` means
 *   *undefined* and zero means *measured zero*, that is the exact confusion the whole
 *   null-handling discipline exists to prevent.
 *
 * So empty gets its own panel that says what it means and, where possible, what to change.
 *
 * Not every failure deserves a retry button
 * -----------------------------------------
 * A 422 is deterministic — the same request returns the same 422 — so offering "Try again"
 * on a bad window teaches a reader that the button does nothing. Retry is shown only when
 * {@link ProblemError.isRetryable} says the request could plausibly succeed unchanged: a
 * 429 whose bucket refills, a 503 during a view refresh, a 504, or a network failure.
 */

/** Shared frame for every non-ready state, so all four occupy the same space. */
function StatePanel({
  icon,
  title,
  children,
  className,
  action,
}: {
  icon: ReactNode
  title: string
  children?: ReactNode
  className?: string
  action?: ReactNode
}) {
  return (
    <div
      className={cn(
        'flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-6 text-center',
        className,
      )}
    >
      <div className="text-muted-foreground [&_svg]:size-6">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="max-w-md text-xs text-muted-foreground">{children}</div>}
      {action}
    </div>
  )
}

/**
 * Loading placeholder.
 *
 * `role="status"` with `aria-busy` on the wrapper and `aria-hidden` on each bar (see
 * `Skeleton`), so a screen reader hears "Loading" once rather than a description of every
 * grey rectangle.
 */
export function LoadingState({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-live="polite"
      className={cn('flex min-h-48 flex-col justify-center gap-3 p-2', className)}
    >
      <span className="sr-only">Loading</span>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton
          key={index}
          className="h-4"
          // Varying widths so the placeholder reads as content arriving rather than as a
          // deliberate striped graphic.
          style={{ width: `${100 - index * 12}%` }}
        />
      ))}
    </div>
  )
}

/**
 * Empty result.
 *
 * @param hasFilters Whether any filter is narrowing the population. Changes the message
 *   from "there is no data here" to "your filters excluded everyone", which are different
 *   situations with different fixes.
 * @param hasLanguageFilter Whether the `language` filter is set. It is the one filter with
 *   **no allowlist** — the API documents that an unknown value narrows the result to
 *   nothing rather than raising — so a typo there produces exactly this empty panel with no
 *   error anywhere. Worth naming explicitly, because it is unguessable otherwise.
 */
export function EmptyState({
  hasFilters = false,
  hasLanguageFilter = false,
  message,
  className,
}: {
  hasFilters?: boolean
  hasLanguageFilter?: boolean
  message?: string
  className?: string
}) {
  return (
    <StatePanel
      icon={hasFilters ? <SearchX /> : <Inbox />}
      title={hasFilters ? 'No rows match these filters' : 'No data in this window'}
      className={className}
    >
      {message ? (
        <p>{message}</p>
      ) : hasFilters ? (
        <p>
          The query ran and returned nothing — a real answer, not a failure. Widen the
          window or clear a filter.
        </p>
      ) : (
        <p>
          The query ran and returned no rows for this reporting window. Try a window inside
          the dataset&rsquo;s activity range.
        </p>
      )}
      {hasLanguageFilter && (
        <p className="mt-2">
          The <code className="font-mono">language</code> filter has no allowlist, so a value
          that does not exist narrows the result to nothing instead of reporting an error.
          Worth checking its spelling first.
        </p>
      )}
    </StatePanel>
  )
}

/** The database is migrated but holds no data. */
export function UnseededState({ className }: { className?: string }) {
  return (
    <StatePanel icon={<Database />} title="The dataset has not been generated yet" className={className}>
      <p>
        The database is reachable and the schema is applied, but no data has been loaded — so
        there is no activity range to build a reporting window from.
      </p>
      <p className="mt-2">
        Run <code className="rounded bg-muted px-1 py-0.5 font-mono">make seed</code> and
        reload.
      </p>
    </StatePanel>
  )
}

/** Choose the icon that matches what actually went wrong. */
function errorIcon(error: ProblemError): ReactNode {
  if (error.status === 0) return <PlugZap />
  if (error.status === 429 || error.status === 504) return <Clock />
  if (error.status === 503) return <Database />
  return <AlertTriangle />
}

/**
 * A failed request.
 *
 * Field errors are listed when present, because on a 422 they name the parameter at fault
 * and that is the only actionable part of the message.
 */
export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: ProblemError
  onRetry?: () => void
  className?: string
}) {
  if (error.isUnseeded) return <UnseededState className={className} />

  // `_accepted` is not a parameter — the API's strict-query check appends it to list what
  // the endpoint does accept. Rendering it as a field name beside the real one would read
  // as two mistakes instead of one mistake and its remedy.
  const fieldErrors = error.fieldErrors.filter((entry) => entry.field !== '_accepted')
  const accepted = error.fieldErrors.find((entry) => entry.field === '_accepted')

  const showRetry = error.isRetryable && onRetry

  return (
    <StatePanel
      icon={errorIcon(error)}
      title={error.problem.title}
      className={cn('border-destructive/40', className)}
      action={
        showRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry} className="mt-2">
            <RefreshCw />
            {/* The limiter tells us when it will work; guessing sooner wastes the attempt. */}
            {error.retryAfterSeconds !== null
              ? `Try again in ${error.retryAfterSeconds}s`
              : 'Try again'}
          </Button>
        ) : undefined
      }
    >
      <p>{error.problem.detail}</p>

      {fieldErrors.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-left">
          {fieldErrors.map((entry) => (
            <li key={`${entry.field}-${entry.message}`}>
              <code className="font-mono text-foreground">{entry.field}</code> — {entry.message}
            </li>
          ))}
        </ul>
      )}

      {accepted && <p className="mt-2">{accepted.message}</p>}

      {/* Only in development. A reader of a deployed dashboard cannot act on a correlation
          id, and the API's own logs are where it is useful. */}
      {IS_DEV && error.requestId && (
        <p className="mt-2 font-mono text-2xs">request id {error.requestId}</p>
      )}
    </StatePanel>
  )
}

export interface QueryBoundaryProps {
  /** True while the first result is loading. Pass react-query's `isPending`. */
  isPending: boolean

  /** The failure, if any. */
  error: ProblemError | null

  /** True when the request succeeded and returned nothing to show. */
  isEmpty?: boolean

  /**
   * True when the query has not been issued because no window exists yet.
   *
   * Rendered as loading rather than empty: the request is still coming, and an empty panel
   * here would tell a reader there is no data when nothing has been asked for.
   */
  isWaiting?: boolean

  /** True when the database holds no data at all. */
  isUnseeded?: boolean

  /** Passed to {@link EmptyState} to choose the right empty message. */
  hasFilters?: boolean
  hasLanguageFilter?: boolean
  emptyMessage?: string

  /** Refetch. Only surfaced for errors that could clear on their own. */
  onRetry?: () => void

  /** Number of skeleton bars while loading. */
  skeletonRows?: number

  className?: string
  children: ReactNode
}

/**
 * Render `children` only when there is something to show.
 *
 * Order matters: unseeded before error, error before empty, empty before ready. An
 * unseeded database also makes every analytics query fail, and reporting the downstream
 * failure would send a reader to debug a query when the fix is one command.
 */
export function QueryBoundary({
  isPending,
  error,
  isEmpty = false,
  isWaiting = false,
  isUnseeded = false,
  hasFilters = false,
  hasLanguageFilter = false,
  emptyMessage,
  onRetry,
  skeletonRows,
  className,
  children,
}: QueryBoundaryProps) {
  if (isUnseeded) return <UnseededState className={className} />
  if (isWaiting || isPending) return <LoadingState rows={skeletonRows} className={className} />
  if (error) return <ErrorState error={error} onRetry={onRetry} className={className} />
  if (isEmpty) {
    return (
      <EmptyState
        hasFilters={hasFilters}
        hasLanguageFilter={hasLanguageFilter}
        message={emptyMessage}
        className={className}
      />
    )
  }
  return <>{children}</>
}
