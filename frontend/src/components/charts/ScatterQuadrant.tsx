import {
  CartesianGrid,
  Cell,
  Label,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'

import { CHART_AXIS, seriesColor } from './palette'
import { EMPTY, formatByUnit, formatNumber, isAbsent } from '@/lib/format'

/**
 * Two metrics against each other, with a break-even line and one point per category.
 *
 * Built for `/marketing/ltv-to-cac`, which is the one endpoint in this API whose question is
 * genuinely two-dimensional: a channel is worth buying when what a user is worth exceeds what
 * they cost, and neither figure means much without the other. A ranked bar chart of the ratio
 * alone hides that a 3.0 ratio on eleven users and a 3.0 ratio on four hundred are different
 * business facts.
 *
 * The quadrant is the API's judgement, not this component's
 * -------------------------------------------------------
 * `quadrant` and `is_profitable` come back as columns. Nothing here recomputes them from the
 * coordinates — the same discipline as a tile taking `sentiment` from the server rather than
 * inferring it from `direction`. A client that derived its own quadrant boundary would
 * eventually disagree with the API about which channels are worth funding, and the
 * disagreement would be invisible.
 *
 * The diagonal is break-even, and it is the only honest reference here
 * ------------------------------------------------------------------
 * Where LTV equals CAC, a channel returns exactly what it cost. Above the line it pays for
 * itself; below, it does not. That threshold is arithmetic rather than a target someone
 * chose, which is why it is drawn and a "good ratio is 3:1" rule of thumb is not.
 *
 * A zero-CAC channel has no ratio, and that is correct
 * ---------------------------------------------------
 * Organic channels cost nothing, so `ltv_to_cac_ratio` is `null` — undefined, not infinite.
 * Those points still plot, at `x = 0`, where they sit trivially above the diagonal. The
 * tooltip shows a dash for the ratio rather than a large number, because dividing by zero
 * produced no measurement.
 */

/** One plotted point. */
export interface QuadrantPoint {
  /** Category name — a channel. */
  label: string

  /** Horizontal position, typically cost. */
  x: number

  /** Vertical position, typically value returned. */
  y: number

  /** Drives the bubble area. Typically the population behind the point. */
  size?: number | null | undefined

  /** The API's own classification, shown in the tooltip verbatim. */
  quadrant?: string

  /** The API's own verdict. Drives the point colour. */
  isProfitable?: boolean

  /** The precomputed ratio, or `null` when the denominator was zero. */
  ratio?: number | null | undefined
}

export interface ScatterQuadrantProps {
  points: readonly QuadrantPoint[]

  /** Axis label for `x`. */
  xLabel: string

  /** Axis label for `y`. */
  yLabel: string

  /** Unit for both axes and the tooltip. Both axes are money for the LTV:CAC chart. */
  unit?: string

  /** What `size` counts, for the tooltip. */
  sizeLabel?: string

  height?: number
}

export function ScatterQuadrant({
  points,
  xLabel,
  yLabel,
  unit = 'usd',
  sizeLabel = 'users',
  height = 320,
}: ScatterQuadrantProps) {
  // The diagonal has to span both axes, so it needs the larger of the two maxima. Computed
  // from the data rather than left to Recharts' auto-domain, which would end the line partway
  // across the plot and make break-even look like a threshold that stops applying.
  const bound = points.reduce((highest, point) => Math.max(highest, point.x, point.y), 0)
  // A little headroom so a point sitting exactly on the maximum is not clipped by the frame.
  const axisMax = bound > 0 ? bound * 1.08 : 1

  const data = points.map((point) => ({
    ...point,
    // Recharts sizes a bubble from a numeric field; an absent population becomes the floor
    // rather than vanishing, since the point's position is still a real measurement.
    z: isAbsent(point.size) ? 0 : point.size,
  }))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 12, right: 16, bottom: 28, left: 8 }}>
        <CartesianGrid stroke={CHART_AXIS.grid} strokeDasharray="3 3" />

        <XAxis
          type="number"
          dataKey="x"
          domain={[0, axisMax]}
          stroke={CHART_AXIS.stroke}
          tick={{ fill: CHART_AXIS.tick, fontSize: 11 }}
          tickFormatter={(value: number) => formatByUnit(value, unit, { compact: true })}
        >
          <Label
            value={xLabel}
            position="insideBottom"
            offset={-16}
            style={{ fill: CHART_AXIS.tick, fontSize: 11 }}
          />
        </XAxis>

        <YAxis
          type="number"
          dataKey="y"
          domain={[0, axisMax]}
          stroke={CHART_AXIS.stroke}
          tick={{ fill: CHART_AXIS.tick, fontSize: 11 }}
          tickFormatter={(value: number) => formatByUnit(value, unit, { compact: true })}
          width={64}
        >
          <Label
            value={yLabel}
            angle={-90}
            position="insideLeft"
            style={{ fill: CHART_AXIS.tick, fontSize: 11, textAnchor: 'middle' }}
          />
        </YAxis>

        {/* Bubble area encodes the population. The floor is non-zero so a small channel is
            still clickable and visible; the ceiling keeps one large channel from covering its
            neighbours. */}
        <ZAxis type="number" dataKey="z" range={[40, 400]} />

        {/* Break-even. `ifOverflow="extendDomain"` is deliberately absent — the domain is
            already square, so the segment spans corner to corner exactly. */}
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: axisMax, y: axisMax },
          ]}
          stroke={CHART_AXIS.tick}
          strokeDasharray="4 4"
          strokeWidth={1}
        >
          <Label
            value="break-even"
            position="insideTopLeft"
            style={{ fill: CHART_AXIS.tick, fontSize: 10 }}
          />
        </ReferenceLine>

        <Tooltip
          // A bespoke tooltip rather than the shared `ChartTooltip`: this chart's point
          // carries five facts of three different units, and the shared one formats every
          // entry with a single unit.
          content={({ active, payload }) => {
            if (!active || !payload || payload.length === 0) return null
            const point = payload[0]?.payload as QuadrantPoint | undefined
            if (!point) return null

            return (
              <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
                <p className="mb-1 font-medium text-popover-foreground">{point.label}</p>
                <ul className="space-y-0.5 text-muted-foreground">
                  <li>
                    {xLabel}: <span className="tabular">{formatByUnit(point.x, unit)}</span>
                  </li>
                  <li>
                    {yLabel}: <span className="tabular">{formatByUnit(point.y, unit)}</span>
                  </li>
                  <li>
                    Ratio:{' '}
                    <span className="tabular">
                      {/* A dash where the API returned null — a zero-cost channel has no
                          ratio, and printing a large number would invent one. */}
                      {isAbsent(point.ratio) ? EMPTY : `${formatNumber(point.ratio, 2)}×`}
                    </span>
                  </li>
                  {!isAbsent(point.size) && (
                    <li>
                      {sizeLabel}: <span className="tabular">{formatNumber(point.size)}</span>
                    </li>
                  )}
                  {point.quadrant && <li className="pt-0.5">{point.quadrant}</li>}
                </ul>
              </div>
            )
          }}
        />

        <Scatter data={data} isAnimationActive={false}>
          {data.map((point, index) => (
            <Cell
              key={point.label}
              // Colour states the API's verdict where it gave one: profitable channels in the
              // positive token, unprofitable in the negative. Falling back to the categorical
              // palette rather than to a neutral grey keeps the points distinguishable when
              // `is_profitable` is absent.
              fill={
                point.isProfitable === undefined
                  ? seriesColor(index)
                  : point.isProfitable
                    ? 'hsl(var(--positive))'
                    : 'hsl(var(--negative))'
              }
              fillOpacity={0.75}
            />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  )
}
