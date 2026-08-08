import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from './ChartTooltip'
import { CHART_AXIS, seriesColor } from './palette'
import { formatDateShort, formatMonthLabel } from '@/lib/dates'
import { formatCompact } from '@/lib/format'

/**
 * A time series, as a line or a stacked area.
 *
 * A `null` breaks the line — it does not bridge it
 * -----------------------------------------------
 * `connectNulls={false}` is Recharts' default and is set explicitly here because it is the
 * single most consequential option in this file. Bridging a gap draws a straight segment
 * between the two surrounding points, and that segment is a figure nobody measured. In an
 * API where `null` means *undefined* — a ratio with an empty denominator, a period not yet
 * elapsed — an interpolated line invents data and does it in the most persuasive form
 * available, a smooth trend.
 *
 * A visible gap is the honest rendering. It also matches the tooltip, which shows an em
 * dash for the same point rather than omitting the series.
 *
 * Stacked areas are for parts of a whole only
 * ------------------------------------------
 * `stacked` is offered because new-vs-returning and MRR movement genuinely decompose a
 * total. It must not be used for independent series: stacking three retention curves
 * produces a top edge at 210% that reads as a total, and no axis label prevents that
 * reading.
 */

/** One plotted series. */
export interface SeriesSpec {
  /** Key in each datum. */
  key: string
  /** Legend and tooltip label. */
  label: string
  /** Overrides the palette slot. */
  color?: string
}

export interface TimeSeriesChartProps<Datum extends Record<string, unknown>> {
  data: readonly Datum[]

  /** Key holding the category value — usually a date. */
  xKey: keyof Datum & string

  series: readonly SeriesSpec[]

  /** `line` for independent series; `area` only for parts of a whole. */
  variant?: 'line' | 'area'

  /** Stack the areas. Ignored for lines. */
  stacked?: boolean

  /** Unit for tooltip formatting, e.g. `users`, `usd`, `percent`. */
  unit?: string

  /** How to render an x-axis tick. `month` for first-of-month buckets. */
  xFormat?: 'date' | 'month' | 'raw'

  /** Hide the legend, for a single-series chart where it is redundant. */
  hideLegend?: boolean

  height?: number
}

export function TimeSeriesChart<Datum extends Record<string, unknown>>({
  data,
  xKey,
  series,
  variant = 'line',
  stacked = false,
  unit,
  xFormat = 'date',
  hideLegend = false,
  height = 280,
}: TimeSeriesChartProps<Datum>) {
  const formatX = (value: unknown): string => {
    // Narrowed rather than passed through `String()`. Recharts types a tick value loosely,
    // and `String()` on an unexpected object yields `[object Object]` — which would then be
    // painted on the axis as if it were a category. An empty tick is the honest rendering of
    // a value this function cannot interpret.
    const text =
      typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
    if (xFormat === 'raw') return text
    if (xFormat === 'month') return formatMonthLabel(text)
    return formatDateShort(text)
  }

  const Chart = variant === 'area' ? AreaChart : LineChart

  return (
    <ResponsiveContainer width="100%" height={height}>
      {/* `data` is readonly here and Recharts declares a mutable array; the component only
          reads it. The spread copies rather than casting, which keeps the immutability
          guarantee at this boundary instead of asserting it away. */}
      <Chart data={[...data]} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        {/* Horizontal only. Vertical gridlines on a daily series over 90 days produce 90
            near-touching verticals that obscure the data they are meant to help read. */}
        <CartesianGrid stroke={CHART_AXIS.grid} strokeDasharray="3 3" vertical={false} />

        <XAxis
          dataKey={xKey}
          tickFormatter={formatX}
          stroke={CHART_AXIS.stroke}
          tick={{ fill: CHART_AXIS.tick, fontSize: 11 }}
          // Recharts drops ticks that would collide rather than rotating or overlapping
          // them, which is the right trade for a 546-day window.
          minTickGap={24}
          tickMargin={8}
        />

        <YAxis
          stroke={CHART_AXIS.stroke}
          tick={{ fill: CHART_AXIS.tick, fontSize: 11 }}
          tickFormatter={(value: number) => formatCompact(value)}
          width={52}
        />

        <Tooltip
          content={<ChartTooltip unit={unit} labelFormatter={formatX} />}
          // Without this the hover highlight is a filled rectangle that covers the plotted
          // area it is describing.
          cursor={{ stroke: CHART_AXIS.grid, strokeWidth: 1 }}
        />

        {!hideLegend && series.length > 1 && (
          <Legend
            iconType="circle"
            iconSize={8}
            wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
          />
        )}

        {series.map((spec, index) =>
          variant === 'area' ? (
            <Area
              key={spec.key}
              type="monotone"
              dataKey={spec.key}
              name={spec.label}
              stackId={stacked ? 'total' : undefined}
              stroke={spec.color ?? seriesColor(index)}
              fill={spec.color ?? seriesColor(index)}
              fillOpacity={stacked ? 0.75 : 0.18}
              strokeWidth={2}
              // See the module docstring: a gap is the honest rendering of an undefined
              // figure, and interpolating across it fabricates a measurement.
              connectNulls={false}
              // Dots on a 546-point series are a solid band. The active dot still appears
              // on hover, so a specific point is still identifiable.
              dot={false}
              activeDot={{ r: 3 }}
            />
          ) : (
            <Line
              key={spec.key}
              type="monotone"
              dataKey={spec.key}
              name={spec.label}
              stroke={spec.color ?? seriesColor(index)}
              strokeWidth={2}
              connectNulls={false}
              dot={false}
              activeDot={{ r: 3 }}
            />
          ),
        )}
      </Chart>
    </ResponsiveContainer>
  )
}
