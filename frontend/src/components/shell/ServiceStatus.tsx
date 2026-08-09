import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CircleCheck,
  CircleHelp,
  CircleX,
  Loader2,
  RefreshCw,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'

import { refreshAnalytics } from '@/api/endpoints'
import { ProblemError } from '@/api/problem'
import { queryKeys, useHealth } from '@/api/queries'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { formatDuration, pluralize } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * Service health, and the one privileged action that fixes the commonest unhealthy state.
 *
 * `degraded` is a 200, so the status must be read from the body
 * -----------------------------------------------------------
 * The API returns 200 for both `ok` and `degraded`, and only `error` gets a 503. A migrated
 * but unseeded database is a *working* container that needs `make seed` — not a failure — and
 * treating any 200 as healthy would show a green light above eleven empty dashboards. So the
 * indicator reads `data.status`, and the three sub-flags below it say which part is missing.
 *
 * The refresh control lives here rather than in its own topbar slot
 * ---------------------------------------------------------------
 * `analytics_ready: false` means the materialized views are stale or empty, and
 * `/admin/refresh-analytics` is the remedy. Putting the remedy inside the panel that reports
 * the problem means a reader finds it at the moment it matters, instead of hunting for a
 * refresh button whose relevance is not obvious from its icon.
 *
 * The admin key is held in component state and never stored
 * -------------------------------------------------------
 * Not in `localStorage`, not in the URL, not in a query key. It is typed, sent once as
 * `X-API-Key`, and cleared on success. Persisting it would leave a credential readable by any
 * script on the origin, in exchange for saving one paste on an action taken rarely.
 */

/** How each `status` value the API can return is presented. */
const STATUS_PRESENTATION: Record<
  string,
  {
    label: string
    icon: LucideIcon
    dot: string
    variant: 'positive' | 'negative' | 'warning'
  }
> = {
  ok: { label: 'Healthy', icon: CircleCheck, dot: 'bg-positive', variant: 'positive' },
  // Amber, not red. The service is answering correctly; something it depends on is not
  // populated. A red light here would send a reader looking for an outage — which is why
  // the `warning` token exists at all.
  degraded: { label: 'Degraded', icon: AlertTriangle, dot: 'bg-warning', variant: 'warning' },
  error: { label: 'Error', icon: CircleX, dot: 'bg-negative', variant: 'negative' },
}

/** One of the three readiness flags, rendered as a row. */
function Flag({ label, ok, hint }: { label: string; ok: boolean; hint: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-2xs text-muted-foreground">{label}</span>
      <span className={cn('text-2xs font-medium', ok ? 'text-positive' : 'text-negative')}>
        {ok ? 'yes' : hint}
      </span>
    </div>
  )
}

export function ServiceStatus() {
  const health = useHealth()
  const queryClient = useQueryClient()

  const [apiKey, setApiKey] = useState('')
  const [concurrent, setConcurrent] = useState(true)

  const refresh = useMutation({
    mutationFn: () => refreshAnalytics(apiKey, concurrent),
    onSuccess: async () => {
      // Every analytics figure on every page was computed from the views that just changed,
      // so the whole scope is invalidated rather than a guessed subset. This is what
      // `queryKeys.all` exists for.
      await queryClient.invalidateQueries({ queryKey: queryKeys.all })
      // Cleared on success only. A failed attempt keeps the key so a mistyped `concurrent`
      // choice can be retried without pasting it again.
      setApiKey('')
    },
  })

  const status = health.data?.data
  const presentation = status ? STATUS_PRESENTATION[status.status] : undefined
  const refreshResult = refresh.data?.data.data

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5 px-2">
          <span
            className={cn(
              'size-2 rounded-full',
              // Unknown is grey rather than green: the health request itself can fail, and
              // an optimistic green while it is failing is the one reading that is never
              // right.
              presentation?.dot ?? (health.isError ? 'bg-negative' : 'bg-muted-foreground'),
              health.isFetching && 'animate-pulse',
            )}
          />
          <span className="hidden text-2xs text-muted-foreground lg:inline">
            {presentation?.label ?? (health.isError ? 'Waking up…' : 'Checking…')}
          </span>
        </Button>
      </PopoverTrigger>

      <PopoverContent align="end" className="w-72">
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium">Service</p>
            {presentation ? (
              <Badge variant={presentation.variant}>
                <presentation.icon className="size-3" />
                {presentation.label}
              </Badge>
            ) : (
              <Badge variant="muted">
                {health.isError ? <CircleX className="size-3" /> : <CircleHelp className="size-3" />}
                {health.isError ? 'Waking up…' : 'Unknown'}
              </Badge>
            )}
          </div>

          {health.isError && (
            <p className="text-2xs text-muted-foreground">
              The backend runs on a free server and sleeps after inactivity.
              It wakes up in 30–60 seconds — please wait, data will load automatically.
            </p>
            </p>
          )}

          {status && (
            <>
              <div className="space-y-1">
                <Flag
                  label="Database connected"
                  ok={status.database_connected}
                  hint="no connection"
                />
                <Flag label="Schema migrated" ok={status.schema_ready} hint="run migrations" />
                <Flag label="Analytics views" ok={status.analytics_ready} hint="stale or empty" />
              </div>

              {status.detail && (
                // The API's own sentence, shown verbatim. It is written for a reader and says
                // which of the three flags is the cause.
                <p className="text-2xs text-muted-foreground">{status.detail}</p>
              )}

              <Separator />

              <div className="flex items-baseline justify-between gap-3 text-2xs text-muted-foreground">
                <span>
                  v{status.version} · {status.environment}
                </span>
                {/* The backend falls back to an in-process cache when Redis is absent, and
                    the two behave differently across replicas — worth surfacing rather than
                    leaving to be inferred from a cache-hit rate. */}
                <span>cache: {status.cache_backend}</span>
              </div>
            </>
          )}

          <Separator />

          <div className="space-y-2">
            <div>
              <p className="text-xs font-medium">Refresh analytics views</p>
              <p className="mt-0.5 text-2xs text-muted-foreground">
                Recomputes the materialized views every dashboard reads. Requires the admin
                key, which is sent once and not stored.
              </p>
            </div>

            <Input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="X-API-Key"
              aria-label="Admin API key"
              className="h-8 text-xs"
              autoComplete="off"
            />

            <div className="flex items-start gap-2">
              <Checkbox
                id="refresh-concurrent"
                checked={concurrent}
                onCheckedChange={(value) => setConcurrent(value === true)}
                className="mt-0.5"
              />
              <Label htmlFor="refresh-concurrent" className="text-2xs font-normal leading-snug">
                Concurrently
                <span className="mt-0.5 block text-muted-foreground">
                  {/* The API exposes this choice rather than deciding, because the Postgres
                      command it maps to cannot run on a view that has never been populated. */}
                  Leave on. Turn it off only for the first refresh after a migration —
                  <code className="font-mono"> REFRESH … CONCURRENTLY</code> fails on a view
                  that has never been populated.
                </span>
              </Label>
            </div>

            <Button
              size="sm"
              className="w-full gap-2"
              disabled={!apiKey.trim() || refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              {refresh.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <RefreshCw />
              )}
              {refresh.isPending ? 'Refreshing…' : 'Refresh'}
            </Button>

            {refresh.isError && (
              <p className="text-2xs text-negative">
                {refresh.error instanceof ProblemError
                  ? refresh.error.message
                  : 'The refresh could not be started.'}
              </p>
            )}

            {refreshResult && (
              <p className="text-2xs text-positive">
                {pluralize(refreshResult.refreshed?.length ?? 0, 'view')} refreshed in{' '}
                {formatDuration(refreshResult.duration_seconds)}.
              </p>
            )}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
