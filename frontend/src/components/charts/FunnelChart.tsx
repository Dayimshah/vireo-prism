import { ArrowDown } from 'lucide-react'

import { seriesColor } from './palette'
import { EMPTY, formatNumber, formatPercent, isAbsent } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * Funnel steps as proportional bars, with the loss between them called out.
 *
 * Built from divs rather than Recharts. A funnel is a list of bars whose widths are a
 * percentage and whose interesting content is the *gap* between consecutive rows — Recharts
 * has no primitive for that, and its `FunnelChart` draws a tapering trapezoid whose area
 * misrepresents the ratios it is meant to show.
 *
 * Width is the share of the entry step, and it is the API's figure
 * --------------------------------------------------------------
 * `pct_of_entry` / `pct_of_signups` comes back computed, so nothing here divides. Recomputing
 * client-side would produce a second answer to the same question, and the two would disagree
 * the moment a step's denominator involved anything more subtle than the first row — which
 * for `/funnel/step-dropoff` it does.
 *
 * The drop is stated as both a count and a rate
 * --------------------------------------------
 * "Lost 412 sessions" and "lost 38%" answer different questions — one sizes the opportunity,
 * the other sizes the problem — and a funnel with only the percentage invites a reader to
 * chase a large rate on a tiny step.
 */

export interface FunnelStep {
  /** Step name, as the API returned it. */
  label: string

  /** Users or sessions reaching this step. */
  count: number

  /** Share of the entry step, already multiplied by the API. */
  pctOfEntry: number | null | undefined

  /**
   * Share of the *previous* step, already multiplied.
   *
   * `null` on the first step, where there is no previous — not zero.
   */
  pctOfPrevious?: number | null | undefined

  /** How many were lost between the previous step and this one. */
  droppedFromPrevious?: number | null | undefined
}

export interface FunnelChartProps {
  steps: readonly FunnelStep[]

  /** What is being counted — `sessions`, `users`. Shown beside each figure. */
  noun?: string

  className?: string
}

export function FunnelChart({ steps, noun = 'sessions', className }: FunnelChartProps) {
  return (
    <ol className={cn('space-y-0', className)}>
      {steps.map((step, index) => {
        // Clamped, and not for cosmetic reasons: `/funnel/step-dropoff` reports transitions
        // whose `to_count` can exceed the entry step when a user re-enters, and a width above
        // 100% would overflow the card rather than reading as a large value.
        const width = isAbsent(step.pctOfEntry) ? 0 : Math.min(100, Math.max(0, step.pctOfEntry))
        const isFirst = index === 0
        const showDrop =
          !isFirst && (!isAbsent(step.droppedFromPrevious) || !isAbsent(step.pctOfPrevious))

        return (
          <li key={`${step.label}-${index}`}>
            {/* The loss sits between the two bars it describes, which is where a reader
                looking at a cliff in the funnel already is. */}
            {showDrop && (
              <div className="flex items-center gap-1.5 py-1 pl-3 text-2xs text-muted-foreground">
                <ArrowDown className="size-3 shrink-0" />
                <span>
                  {isAbsent(step.droppedFromPrevious)
                    ? EMPTY
                    : `${formatNumber(step.droppedFromPrevious)} ${noun} lost`}
                </span>
                {!isAbsent(step.pctOfPrevious) && (
                  <span>
                    {/* The API reports the *survival* rate between steps; the loss is its
                        complement. Stated as the loss because that is the number a reader is
                        looking for at a step change, and computing it here from one
                        subtraction cannot disagree with the source. */}
                    ({formatPercent(100 - step.pctOfPrevious)} of the previous step)
                  </span>
                )}
              </div>
            )}

            <div className="space-y-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <span className="text-xs font-medium">{step.label}</span>
                <span className="text-2xs text-muted-foreground">
                  <span className="tabular text-foreground">{formatNumber(step.count)}</span>{' '}
                  {noun} · {formatPercent(step.pctOfEntry)} of entry
                </span>
              </div>

              {/* The track makes the empty space legible. Without it a 4% bar reads as a
                  rendering glitch rather than as a step almost nobody reaches. */}
              <div className="h-6 w-full overflow-hidden rounded bg-muted">
                <div
                  className="h-full rounded transition-[width] duration-300 motion-reduce:transition-none"
                  style={{
                    width: `${width}%`,
                    backgroundColor: seriesColor(index),
                  }}
                  // The bar is decoration: every figure it encodes is already in the text
                  // above it, so announcing it again would make a screen reader read each
                  // step twice.
                  aria-hidden="true"
                />
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
