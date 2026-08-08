import type { CacheState, ResponseInfo } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

/**
 * Shows whether a figure came from cache, and how long the server took.
 *
 * Present because it is the honest way to explain why the same chart takes 400ms once and
 * 4ms afterwards, and because `NONE` and `MISS` are genuinely different states that would
 * otherwise be invisible:
 *
 * * `HIT` — served from cache.
 * * `MISS` — the endpoint caches, and this request was not in it.
 * * `PARTIAL` — a composite endpoint mixed both. `/overview` reports this as its **steady
 *   state**, by design: it does not cache its own result because its six inputs already
 *   do, so `PARTIAL` there is not a symptom.
 * * `NONE` — the endpoint performs no cache lookup at all. `/meta/filters` reads the
 *   in-memory dimension catalogue, so it reports `NONE` rather than `MISS` forever.
 * * `UNKNOWN` — the `X-Cache` header was not readable. Cross-origin, JavaScript can only
 *   see it because the API lists it in CORS `expose_headers`; if that were removed, every
 *   response would read `UNKNOWN` and this badge is where that would show up.
 */

const CACHE_EXPLANATIONS: Record<CacheState, string> = {
  HIT: 'Served from cache. The query was not re-run.',
  MISS: 'Computed on this request and stored for next time.',
  PARTIAL:
    'Some of the underlying queries were cached and some were not. Expected on the overview, which composes six separately-cached inputs.',
  NONE: 'This endpoint does not cache — it reads state the API already holds in memory.',
  UNKNOWN:
    'The X-Cache header was not readable. If this shows on every response, the API is no longer exposing it to the browser via CORS.',
}

const CACHE_VARIANTS: Record<CacheState, 'positive' | 'secondary' | 'muted' | 'outline'> = {
  HIT: 'positive',
  MISS: 'secondary',
  PARTIAL: 'secondary',
  NONE: 'muted',
  UNKNOWN: 'outline',
}

export function CacheBadge({ info }: { info: ResponseInfo | undefined }) {
  if (!info) return null

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={CACHE_VARIANTS[info.cache]} className="cursor-help font-mono">
          {info.cache}
          {info.serverMs !== null && (
            <span className="opacity-70">
              {/* Sub-millisecond responses are common on a cache hit; rounding them to `0ms`
                  would read as a broken measurement rather than a fast one. */}
              {info.serverMs < 1 ? '<1ms' : `${Math.round(info.serverMs)}ms`}
            </span>
          )}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        <p>{CACHE_EXPLANATIONS[info.cache]}</p>
        {info.serverMs !== null && (
          <p className="mt-1 text-muted-foreground">
            {/* Named as server-side explicitly: a slow chart with a fast figure here is a
                transfer or render problem, not a query problem. */}
            {info.serverMs.toFixed(1)}ms in the API, excluding network time.
          </p>
        )}
        {info.requestId && (
          <p className="mt-1 font-mono text-2xs text-muted-foreground">{info.requestId}</p>
        )}
      </TooltipContent>
    </Tooltip>
  )
}
