import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from './ChartTooltip'
import { CHART_AXIS, seriesColor } from './palette'
import { formatCompact } from '@/lib/format'

/**
 * A bar chart over categories rather than time.
 *
 * The shape most of this API returns: a ranking (top titles, country league table), a
 * distribution (session depth, events per session), or a bucketed relationship
 * (conversion by watch decile). Distinct from {@link TimeSeriesChart} because the x-axis is a
 * set of names with no order beyond the one the query chose, so nothing here interpolates,
 * connects, or reads a gap as a trend.
 *
 * Horizontal by default
 * ---------------------
 * Category labels in this dataset are titles, country names and RFM segment names — long
 * enough that vertical bars would either rotate the labels 45° or drop half of them. A
 * horizontal layout gives each label a full line of room, and rankings read top-to-bottom
 * anyway.
 *
 * One series is coloured per bar; several are coloured per series
 * -------------------------------------------------------------
 * With a single series the bars *are* the categories, so each gets its own palette slot and
 * the legend is redundant. With several, colour has to mean the series or the legend cannot
 * work — so every bar of a series shares one colour. Getting this backwards produces a chart
 * where colour means two things at once.
 */

/** One plotted series. */
export interface BarSeries {
  /** Key in each datum. */
  key: string
  /** Legend and tooltip label. */
  label: string
  /** Overrides the palette slot. */
  color?: string
}

export interface CategoryBarChartProps<Datum extends Record<string, unknown>> {
  data: readonly Datum[]

  /** Key holding the category name. */
  categoryKey: keyof Datum & string

  series: readonly BarSeries[]

  /** `horizontal` puts categories on the y-axis. */
  layout?: 'horizontal' | 'vertical'

  /** Stack the bars. Only for parts of a whole. */
  stacked?: boolean

  /** Unit for tooltip formatting, e.g. `hours`, `usd`, `percent`. */
  unit?: string

  /** Width reserved for category labels in the horizontal layout. */
  categoryWidth?: number

  /** Hide the legend, for a single-series chart where it says nothing. */
  hideLegend?: boolean

  /** Shorten a long category label for the axis. The tooltip still shows it in full. */
  formatCategory?: (value: string) => string

  height?: number
}

export function CategoryBarChart<Datum extends Record<string, unknown>>({
  data,
  categoryKey,
  series,
  layout = 'horizontal',
  stacked = false,
  unit,
  categoryWidth = 120,
  hideLegend = false,
  formatCategory,
  height = 280,
}: CategoryBarChartProps<Datum>) {
  const isHorizontal = layout === 'horizontal'
  const perBarColour = series.length === 1

  const formatCategoryTick = (value: unknown): string => {
    // Narrowed rather than coerced with `String()`, which would paint `[object Object]` on
    // the axis as though it were a category name.
    const text = typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
    return formatCategory ? formatCategory(text) : text
  }

  // Shared axis styling. The category/value distinction is applied at the call sites below
  // rather than by swapping whole elements, because Recharts locates its axes by walking
  // `children` — a conditional pair wrapped in a Fragment is a shape it is not obliged to
  // see through, and the failure mode is a chart that renders with no axes at all.
  const axisStyle = {
    stroke: CHART_AXIS.stroke,
    tick: { fill: CHART_AXIS.tick, fontSize: 11 },
  } as const

  return (
    <ResponsiveContainer width="100%" height={height}>
      {/* Spread rather than cast: `data` is readonly here and Recharts declares a mutable
          array, and the component only reads it. */}
      <BarChart
        data={[...data]}
        layout={isHorizontal ? 'vertical' : 'horizontal'}
        margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
      >
        {/* Gridlines only on the value axis. On the category axis they would draw one line
            per bar, which is a fence in front of the data. */}
        <CartesianGrid
          stroke={CHART_AXIS.grid}
          strokeDasharray="3 3"
          horizontal={!isHorizontal}
          vertical={isHorizontal}
        />

        <XAxis
          {...axisStyle}
          // In the horizontal layout the x-axis carries the magnitude; in the vertical one it
          // carries the category names.
          type={isHorizontal ? 'number' : 'category'}
          dataKey={isHorizontal ? undefined : categoryKey}
          tickFormatter={
            isHorizontal ? (value: number) => formatCompact(value) : formatCategoryTick
          }
          // `interval={0}` forces every category label to render. Only meaningful on the
          // category axis, where dropping labels would leave unlabelled bars.
          interval={isHorizontal ? 'preserveEnd' : 0}
          tickMargin={8}
        />

        <YAxis
          {...axisStyle}
          type={isHorizontal ? 'category' : 'number'}
          dataKey={isHorizontal ? categoryKey : undefined}
          tickFormatter={
            isHorizontal ? formatCategoryTick : (value: number) => formatCompact(value)
          }
          interval={isHorizontal ? 0 : 'preserveEnd'}
          width={isHorizontal ? categoryWidth : 52}
        />

        <Tooltip
          content={<ChartTooltip unit={unit} />}
          // A translucent fill rather than the default opaque one, which covers the bar it
          // is describing.
          cursor={{ fill: CHART_AXIS.grid, fillOpacity: 0.25 }}
        />

        {!hideLegend && series.length > 1 && (
          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
        )}

        {series.map((spec, seriesIndex) => (
          <Bar
            key={spec.key}
            dataKey={spec.key}
            name={spec.label}
            stackId={stacked ? 'total' : undefined}
            fill={spec.color ?? seriesColor(seriesIndex)}
            radius={isHorizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]}
            // Recharts animates from zero on every data change, which on a filter change
            // reads as the bars being recomputed rather than replaced.
            isAnimationActive={false}
          >
            {/* Per-bar colour for a single series only — see the module docstring. */}
            {perBarColour &&
              !spec.color &&
              data.map((datum, index) => (
                <Cell key={String(datum[categoryKey] ?? index)} fill={seriesColor(index)} />
              ))}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
