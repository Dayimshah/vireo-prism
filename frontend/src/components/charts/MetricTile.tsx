import { ArrowDown, ArrowRight, ArrowUp, Info } from 'lucide-react'

import type { Tile } from '@/api/endpoints'
import { Card } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EMPTY, formatByUnit, formatDelta, isAbsent } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * One headline figure with its period-over-period change.
 *
 * Colour comes from `sentiment`, never from `direction`
 * ----------------------------------------------------
 * The API returns both, as separate fields, precisely because they are separate questions:
 * churn moving up is `direction: "up"` and `sentiment: "bad"`. The arrow shows which way
 * the number moved; the colour shows whether that is good news. Deriving the colour from the
 * direction is how a rising bad number ends up green — and the server has already done the
 * work of deciding, using `higher_is_better`, so there is nothing for this component to
 * infer.
 *
 * `grain` is shown because tiles of different grain are not comparable
 * ------------------------------------------------------------------
 * `avg_dau` is a **mean** of daily figures — summing 90 daily DAU counts a daily visitor 90
 * times — while `sessions` is a **window total** and `mrr_usd` is the **latest month**,
 * because MRR is a recurring stock and adding months together is meaningless. A reader
 * comparing two tiles needs to know they were reduced differently, so the grain is rendered
 * rather than hidden in a tooltip.
 */

/** Human labels for the API's `Grain` enum. */
const GRAIN_LABELS: Record<string, string> = {
  window_total: 'total for the window',
  window_mean: 'mean across the window',
  latest_month: 'latest full month',
}

/** Arrow for the direction the figure moved. */
function DirectionIcon({ direction }: { direction: Tile['direction'] }) {
  if (direction === 'up') return <ArrowUp className="size-3.5" />
  if (direction === 'down') return <ArrowDown className="size-3.5" />
  // `flat` and `unknown` both get the horizontal arrow. They differ in meaning — no change
  // versus not computable — and the delta text below already distinguishes them, so a third
  // glyph would add noise without adding information.
  return <ArrowRight className="size-3.5" />
}

/** Text colour for the change, taken from the server's judgement. */
function sentimentClass(sentiment: Tile['sentiment']): string {
  if (sentiment === 'good') return 'text-positive'
  if (sentiment === 'bad') return 'text-negative'
  return 'text-muted-foreground'
}

export function MetricTile({ tile, className }: { tile: Tile; className?: string }) {
  const hasDelta = !isAbsent(tile.delta)

  return (
    <Card className={cn('flex flex-col gap-2 p-4', className)}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{tile.label}</p>
        {tile.description && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={`What ${tile.label} measures`}
              >
                <Info className="size-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent>
              <p>{tile.description}</p>
              {GRAIN_LABELS[tile.grain] && (
                <p className="mt-1 text-muted-foreground">
                  Reported as the {GRAIN_LABELS[tile.grain]}. Tiles of different grain are not
                  comparable.
                </p>
              )}
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      <p className="tabular text-2xl font-semibold leading-none">
        {/* An absent value renders as a dash, not as zero. A tile reading `0` for an
            undefined ratio — stickiness with an empty denominator — is a measurement the
            data does not support. */}
        {formatByUnit(tile.value, tile.unit, { compact: true })}
      </p>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs">
        {hasDelta ? (
          <span className={cn('inline-flex items-center gap-0.5 font-medium', sentimentClass(tile.sentiment))}>
            <DirectionIcon direction={tile.direction} />
            {/* Percentage where the API computed one, absolute otherwise. `delta_pct` is
                null when the previous value was zero — growth from nothing has no
                percentage — and printing an absolute change there is the honest fallback. */}
            {isAbsent(tile.delta_pct)
              ? formatDelta(tile.delta)
              : formatDelta(tile.delta_pct, { percent: true })}
          </span>
        ) : (
          <span className="text-muted-foreground">{EMPTY} no comparison</span>
        )}

        <span className="text-muted-foreground">
          vs {formatByUnit(tile.previous, tile.unit, { compact: true })} previous
        </span>
      </div>
    </Card>
  )
}

/**
 * A row of tiles, sized so six fit on a wide screen and stack sensibly below.
 *
 * `auto-fit` with a `minmax` floor rather than fixed breakpoints: the overview returns six
 * tiles today and the count is the server's to change, so the grid adapts instead of
 * needing a matching column count here.
 */
export function MetricTileGrid({ tiles }: { tiles: readonly Tile[] }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(11rem,1fr))] gap-3">
      {tiles.map((tile) => (
        <MetricTile key={tile.key} tile={tile} />
      ))}
    </div>
  )
}
