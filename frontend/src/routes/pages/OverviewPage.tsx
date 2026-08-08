import { usePanel } from '@/api/panel'
import { MetricTileGrid } from '@/components/charts/MetricTile'
import { CacheBadge } from '@/components/state/CacheBadge'
import { QueryBoundary } from '@/components/state/QueryBoundary'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatMonthLabel, formatWindow } from '@/lib/dates'
import { pluralize } from '@/lib/format'

/**
 * Headline figures for the window, against the period before it.
 *
 * One endpoint, and it is the only one that returns an object rather than rows — so this page
 * reads `payload.data` and its `rows` array is permanently empty. That asymmetry is why
 * `usePanel` computes `isEmpty` from the response shape rather than from a row count.
 *
 * Three things this page has to say out loud
 * -----------------------------------------
 * **The comparison window is the server's.** `/overview` computes its own equal-length
 * preceding period and returns it as `comparison_window`. Rendering a locally-derived
 * previous window beside server-derived deltas would eventually disagree with the numbers it
 * is labelling.
 *
 * **Tiles of different grain are not comparable.** `avg_dau` is a mean across the window,
 * `sessions` is a total, and `mrr_usd` is the latest month — because MRR is a recurring stock
 * and summing months is meaningless. Each tile carries its own `grain` and `MetricTile`
 * renders it; this page adds the `revenue_month` the monetary tiles actually describe, which
 * matters most when the window is narrower than a month and that month is therefore partial.
 *
 * **An empty window does not blank the tiles.** Every query behind the overview builds its
 * own date spine and LEFT JOINs onto it, so a window outside the dataset returns explicit
 * zeros rather than nothing — the tiles read `0` with real deltas. Only genuinely undefined
 * figures arrive `null`: a ratio whose denominator was empty, like `stickiness_pct` or
 * `arpu_usd`. `MetricTile` renders those as a dash, never as zero.
 */
export function OverviewPage() {
  const overview = usePanel('/overview')
  const data = overview.payload?.data

  const tiles = data?.tiles ?? []

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div className="min-w-0">
            <CardTitle className="text-sm">Headline figures</CardTitle>
            {data && (
              <p className="mt-1 text-xs text-muted-foreground">
                {formatWindow(data.window)} ({pluralize(data.window.days, 'day')}), compared with{' '}
                {formatWindow(data.comparison_window)}
              </p>
            )}
          </div>

          <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
            {/* The API reports whether filters narrowed the population. Shown because a
                headline figure a reader intends to quote elsewhere is a different number
                when it describes a segment, and nothing else on the tile says so. */}
            {data?.is_filtered && <Badge variant="warning">filtered population</Badge>}
            <CacheBadge info={overview.info} />
          </div>
        </CardHeader>

        <CardContent>
          <QueryBoundary
            {...overview.boundary}
            // The endpoint returns one object, so `isEmpty` can never fire from a row count.
            // A response whose `tiles` list is empty is the real empty case here.
            isEmpty={overview.boundary.isEmpty || (data !== undefined && tiles.length === 0)}
            emptyMessage="The overview returned no tiles for this window."
            skeletonRows={4}
          >
            <div className="space-y-3">
              <MetricTileGrid tiles={tiles} />

              {/* `revenue_month` is the month the monetary tiles describe. Returned precisely
                  because a window narrower than a month yields a partial one, and a reader
                  comparing MRR against a 14-day window would otherwise assume the two
                  cover the same period. */}
              {data?.revenue_month && (
                <p className="text-2xs text-muted-foreground">
                  Revenue figures describe {formatMonthLabel(data.revenue_month)}. MRR is a
                  recurring monthly stock, so it is reported for the latest month in the window
                  rather than summed across it.
                </p>
              )}
            </div>
          </QueryBoundary>
        </CardContent>
      </Card>
    </div>
  )
}
