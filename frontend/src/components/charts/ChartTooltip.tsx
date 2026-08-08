import type { TooltipProps } from 'recharts'
import type { NameType, ValueType } from 'recharts/types/component/DefaultTooltipContent'

import { EMPTY, formatByUnit } from '@/lib/format'

/**
 * Tooltip for every chart.
 *
 * Recharts' default tooltip renders raw values, which loses two things this dataset needs:
 * the unit (so `27330.9` reads as hours rather than a count) and the distinction between a
 * `null` and a zero.
 *
 * A `null` entry is shown as an em dash with its series name intact, rather than being
 * omitted. Omitting it would tell a reader the series does not exist at that point, when
 * what is true is that its value is undefined there — an empty denominator, a period not
 * yet elapsed. Those are different facts and the tooltip is usually where a reader goes to
 * find out which one they are looking at.
 */
export interface ChartTooltipProps extends TooltipProps<ValueType, NameType> {
  /** Unit for the values, passed to {@link formatByUnit}. */
  unit?: string

  /** Format the header label. Defaults to the raw category value. */
  labelFormatter?: (label: unknown) => string
}

export function ChartTooltip({
  active,
  payload,
  label,
  unit,
  labelFormatter,
}: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null

  const heading = labelFormatter ? labelFormatter(label) : String(label ?? '')

  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      {heading && <p className="mb-1 font-medium text-popover-foreground">{heading}</p>}
      <ul className="space-y-0.5">
        {payload.map((entry, index) => {
          // Recharts types `value` as `ValueType`, which includes arrays for range series.
          // None of these charts use one, and a non-numeric value is not something to
          // guess at — it renders as absent rather than as `[object Object]`.
          const raw = entry.value
          const numeric = typeof raw === 'number' ? raw : null

          return (
            <li key={`${String(entry.name)}-${index}`} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-muted-foreground">{String(entry.name)}</span>
              <span className="ml-auto font-mono tabular">
                {numeric === null ? EMPTY : formatByUnit(numeric, unit)}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
