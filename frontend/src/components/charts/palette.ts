/**
 * Series colours and the cohort heatmap ramp.
 *
 * Colours are returned as `hsl(var(--chart-N))` rather than literals, so a series follows
 * the theme without any component knowing which theme is active. SVG `fill` and `stroke`
 * accept a CSS colour function, so the variable resolves the same way it would in a
 * stylesheet — and switching to dark mode recolours every chart with no re-render.
 */

/** How many distinct categorical colours exist. */
export const SERIES_COUNT = 8

/**
 * Colour for series `index`, wrapping if there are more series than colours.
 *
 * Eight, because the widest categorical dimension in the dataset is persona (8 values).
 * Channel has 12, and its charts rank and truncate rather than plotting every one — at
 * twelve simultaneous lines the colours stop being distinguishable regardless of palette,
 * so the honest fix is fewer series rather than more hues.
 */
export function seriesColor(index: number): string {
  // `%` after a floor guards a negative or fractional index from producing `--chart-NaN`,
  // which resolves to nothing and paints the series black.
  const slot = ((Math.floor(index) % SERIES_COUNT) + SERIES_COUNT) % SERIES_COUNT
  return `hsl(var(--chart-${slot + 1}))`
}

/** All eight, for a legend or a static mapping. */
export const SERIES_COLORS: readonly string[] = Array.from({ length: SERIES_COUNT }, (_, index) =>
  seriesColor(index),
)

/**
 * Assign a stable colour to each named series.
 *
 * Stable in the sense that matters: the same list of names always produces the same
 * mapping, so "Binge Watcher" is the same colour on every chart of the same dimension. Not
 * stable across *different* lists — a filtered persona list shifts the assignments, which
 * is why a legend is always rendered rather than relying on colour memory.
 */
export function assignColors(names: readonly string[]): Record<string, string> {
  const mapping: Record<string, string> = {}
  names.forEach((name, index) => {
    mapping[name] = seriesColor(index)
  })
  return mapping
}

/** Steps in the heatmap ramp. */
const HEAT_STEPS = 6

/**
 * Colour for a cohort-matrix cell.
 *
 * @param value The cell's value, or `null` when the period has not elapsed.
 * @param max The largest value in the matrix, used to normalise.
 *
 * A `null` cell returns the neutral `--heat-null`, deliberately outside the ramp. On the
 * ramp it would land at step 0 and read as near-zero retention, which is a wrong reading of
 * correct data: the API returns `null` for a cell whose observation window has not elapsed,
 * meaning *not yet observable*, never *nobody retained*.
 */
export function heatColor(value: number | null | undefined, max: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return 'hsl(var(--heat-null))'
  }

  // A matrix of all zeros, or a single-cohort matrix, gives max === 0. Dividing would be
  // NaN, and every cell is genuinely at the bottom of the range.
  if (max <= 0) return 'hsl(var(--heat-0))'

  const ratio = Math.min(1, Math.max(0, value / max))
  const step = Math.min(HEAT_STEPS - 1, Math.round(ratio * (HEAT_STEPS - 1)))
  return `hsl(var(--heat-${step}))`
}

/**
 * Text colour that stays readable on a heatmap cell.
 *
 * The ramp's upper steps are dark in light mode and light in dark mode, so the label has to
 * flip with the cell rather than with the theme. Returning a Tailwind class rather than a
 * colour keeps both variants in `index.css`.
 */
export function heatTextClass(value: number | null | undefined, max: number): string {
  if (value === null || value === undefined || Number.isNaN(value) || max <= 0) {
    return 'text-muted-foreground'
  }
  const ratio = Math.min(1, Math.max(0, value / max))
  // Above roughly 60% of the range the cell is dark enough (light mode) or bright enough
  // (dark mode) that the default foreground loses contrast.
  return ratio > 0.6 ? 'text-heat-0' : 'text-foreground'
}

/**
 * Axis and grid colours for Recharts.
 *
 * Recharts sets these as SVG attributes rather than classes, so they cannot be Tailwind
 * utilities and have to be resolved colour values.
 */
export const CHART_AXIS = {
  stroke: 'hsl(var(--border))',
  tick: 'hsl(var(--muted-foreground))',
  grid: 'hsl(var(--border))',
} as const
