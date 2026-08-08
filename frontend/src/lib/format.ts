/**
 * Number, money, duration and percentage formatting.
 *
 * The one rule this module exists to enforce
 * ------------------------------------------
 * **A `null` from this API is an undefined figure, never a zero.** A ratio with an empty
 * denominator, a cohort cell whose observation period has not elapsed, a churn rate for
 * a segment with no churners — all arrive as `null`, and all are statements that the
 * number does not exist rather than that it is small.
 *
 * So every formatter here takes `number | null | undefined` and renders {@link EMPTY} for
 * the absent case. Nothing coerces. The failure being prevented is specific and quiet:
 * `value || 0` turns an undefined ratio into `0.0%`, which plots on an axis, trends
 * against its neighbours, and reads as a real measurement of zero. A dash cannot be
 * misread that way.
 *
 * Percentages arrive pre-multiplied
 * ---------------------------------
 * The API returns `stickiness_pct: 51.1` meaning 51.1%, not 0.511. Every `_pct` column in
 * the 48 queries is already multiplied and rounded by SQL. {@link formatPercent}
 * therefore appends a sign and does **not** scale — passing it a 0-1 ratio would render
 * `0.5%` for a half, and that is a factor-of-100 error no axis label would reveal.
 * {@link formatRatioAsPercent} exists for the genuinely fractional case.
 *
 * Decimals arrive as JSON numbers
 * -------------------------------
 * `app/schemas/base.py` serialises `Decimal` as a JSON number rather than a string,
 * deliberately, so these functions receive `number` and never parse. The exactness that
 * a string would have preserved was already spent by SQL's rounding upstream.
 */

/**
 * Rendered in place of an absent figure.
 *
 * An em dash rather than `"N/A"`, `"-"` or an empty cell: it is visually distinct from a
 * minus sign at a glance, it does not read as an abbreviation someone has to decode, and
 * an empty cell is indistinguishable from a rendering bug.
 */
export const EMPTY = '—'

/** True when a value is absent and must render as {@link EMPTY} rather than a number. */
export function isAbsent(value: number | null | undefined): value is null | undefined {
  // NaN is included deliberately. It should never arrive from the API, and if it does —
  // a malformed payload, a division performed client-side — rendering "NaN" in a tile is
  // strictly worse than rendering a dash.
  return value === null || value === undefined || Number.isNaN(value)
}

/**
 * The locale used for every figure in the app.
 *
 * Fixed rather than taken from the browser. A dashboard whose thousands separator depends
 * on the reader's machine cannot be screenshotted into a shared deck without the numbers
 * changing shape between colleagues, and `en-IN` grouping (1,09,2554) would disagree with
 * the `en-US` grouping in the API's own documentation examples.
 */
const LOCALE = 'en-US'

/** Cache of `Intl.NumberFormat` instances, keyed by their options. */
const formatterCache = new Map<string, Intl.NumberFormat>()

/**
 * Return a cached `Intl.NumberFormat`.
 *
 * Constructing one costs roughly a hundred microseconds, which is invisible once and
 * material in a 12-column cohort matrix over 18 monthly cohorts — 216 cells, each
 * formatted on every re-render. The cache key is the serialised options, so callers do
 * not have to hold instances themselves.
 */
function formatter(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = JSON.stringify(options)
  let cached = formatterCache.get(key)
  if (!cached) {
    cached = new Intl.NumberFormat(LOCALE, options)
    formatterCache.set(key, cached)
  }
  return cached
}

/**
 * Format a count: DAU, sessions, users, events.
 *
 * Integers by default, because these are counts of things and `1,092,554.0` events is a
 * false precision. `avg_dau` is a mean and genuinely fractional, which is why
 * `maximumFractionDigits` is a parameter rather than fixed at zero.
 */
export function formatNumber(
  value: number | null | undefined,
  maximumFractionDigits = 0,
): string {
  if (isAbsent(value)) return EMPTY
  return formatter({ maximumFractionDigits, minimumFractionDigits: 0 }).format(value)
}

/**
 * Format a figure compactly for a tile or an axis tick: `1.1M`, `27.3K`.
 *
 * For constrained space only. A compact figure has lost digits — `27330.9` watch hours
 * renders `27.3K` — so anything a reader might want to read exactly, or copy, should use
 * {@link formatNumber}. Below 1,000 this falls through to the plain form, since `847`
 * compacts to `847` anyway and the compact notation would only add a decimal point.
 */
export function formatCompact(value: number | null | undefined): string {
  if (isAbsent(value)) return EMPTY
  if (Math.abs(value) < 1000) return formatNumber(value)
  return formatter({ notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

/**
 * Format money in USD.
 *
 * The dataset is denominated in USD throughout — `mrr_usd`, `arpu_usd`, `cac_usd` — so
 * the currency is fixed rather than configurable, and the column names carry the unit
 * explicitly.
 *
 * Two decimal places by default because these are already-rounded currency amounts and
 * `$12.3` reads as truncated. Pass `maximumFractionDigits: 0` for an axis, where the
 * cents are noise.
 */
export function formatCurrency(
  value: number | null | undefined,
  options: { maximumFractionDigits?: number; compact?: boolean } = {},
): string {
  if (isAbsent(value)) return EMPTY

  const { compact = false } = options
  const maximumFractionDigits = options.maximumFractionDigits ?? (compact ? 1 : 2)

  return formatter({
    style: 'currency',
    currency: 'USD',
    notation: compact ? 'compact' : 'standard',
    maximumFractionDigits,
    // Matched to the maximum so `$12.5` cannot appear beside `$12.50` in one column.
    minimumFractionDigits: compact ? 0 : maximumFractionDigits,
  }).format(value)
}

/**
 * Format an already-multiplied percentage: `51.1` becomes `51.1%`.
 *
 * Does **not** scale. See the module docstring — every `_pct` column in this API arrives
 * pre-multiplied, and scaling here would be a silent factor-of-100 error. Use
 * {@link formatRatioAsPercent} for a 0-1 fraction.
 */
export function formatPercent(
  value: number | null | undefined,
  maximumFractionDigits = 1,
): string {
  if (isAbsent(value)) return EMPTY
  return `${formatter({ maximumFractionDigits, minimumFractionDigits: 0 }).format(value)}%`
}

/**
 * Format a 0-1 fraction as a percentage: `0.511` becomes `51.1%`.
 *
 * Separate from {@link formatPercent} rather than a flag on it, so the two cannot be
 * confused at a call site. A boolean argument would make the wrong choice look identical
 * to the right one.
 */
export function formatRatioAsPercent(
  value: number | null | undefined,
  maximumFractionDigits = 1,
): string {
  if (isAbsent(value)) return EMPTY
  return formatPercent(value * 100, maximumFractionDigits)
}

/**
 * Format a signed delta, always carrying its sign: `+41.4`, `-3.0`.
 *
 * The sign is the point. A tile showing `41.4` beside "vs previous period" is ambiguous
 * about direction in a way `+41.4` is not, and `signDisplay: 'exceptZero'` keeps a true
 * zero as a bare `0` rather than the meaningless `+0`.
 */
export function formatDelta(
  value: number | null | undefined,
  options: { maximumFractionDigits?: number; percent?: boolean } = {},
): string {
  if (isAbsent(value)) return EMPTY

  const { percent = false, maximumFractionDigits = 1 } = options
  const formatted = formatter({
    maximumFractionDigits,
    minimumFractionDigits: 0,
    signDisplay: 'exceptZero',
  }).format(value)

  return percent ? `${formatted}%` : formatted
}

/**
 * Format a duration given in seconds as `4m 12s`, `1h 04m`, or `18s`.
 *
 * Session durations in this dataset span single-digit seconds (an immediate bounce) to
 * several hours (a film watched through), so a fixed unit would render either `0.0h` or
 * `7332s`. The largest two units are shown and the rest dropped, because a third unit
 * adds noise at every magnitude.
 *
 * Seconds are the API's unit throughout: every duration column goes through
 * `EXTRACT(EPOCH FROM ...)` before it leaves Postgres.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (isAbsent(seconds)) return EMPTY

  // A negative duration is not a real measurement; showing it is more honest than
  // rendering `-0m 5s`, which reads like a formatting artefact.
  if (seconds < 0) return EMPTY

  const total = Math.round(seconds)
  if (total < 60) return `${total}s`

  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60

  if (hours > 0) {
    // Minutes padded so `1h 04m` and `1h 40m` are the same width in a column.
    return `${hours}h ${String(minutes).padStart(2, '0')}m`
  }
  return `${minutes}m ${String(secs).padStart(2, '0')}s`
}

/**
 * Format a count of hours, as the overview's `watch_hours` tile reports it.
 *
 * Distinct from {@link formatDuration}, which takes seconds and breaks them into units.
 * This one is a magnitude in a single named unit and stays that way.
 */
export function formatHours(value: number | null | undefined, compact = false): string {
  if (isAbsent(value)) return EMPTY
  return `${compact ? formatCompact(value) : formatNumber(value, 1)}h`
}

/**
 * The `unit` values the API's `Unit` enum actually declares.
 *
 * Exactly these five — checked against the generated schema rather than guessed. An earlier
 * draft of this type also listed `count` and `seconds`, which the API never returns; a type
 * that claims more than the server sends is a type nobody can rely on.
 *
 * {@link formatByUnit} still handles those two strings, because it is also used for table
 * columns whose unit this app decides locally, and its parameter is a plain `string` for
 * that reason.
 */
export type TileUnit = 'users' | 'sessions' | 'hours' | 'percent' | 'usd'

/**
 * Format a value according to the unit the API declared for it.
 *
 * The overview endpoint returns `unit` alongside every tile, so the presentation layer
 * does not have to carry a parallel table of which metric is money and which is a
 * percentage — a table that would drift the first time a tile was added server-side.
 *
 * An unrecognised unit falls through to a plain number rather than throwing. A new unit
 * appearing server-side should render a slightly plain tile, not white-screen the page.
 */
export function formatByUnit(
  value: number | null | undefined,
  unit: string | null | undefined,
  options: { compact?: boolean } = {},
): string {
  if (isAbsent(value)) return EMPTY

  const { compact = false } = options

  switch (unit) {
    case 'usd':
      return formatCurrency(value, { compact })
    case 'percent':
      return formatPercent(value)
    case 'hours':
      return formatHours(value, compact)
    case 'seconds':
      return formatDuration(value)
    case 'users':
    case 'sessions':
    case 'count':
      // Means such as `avg_dau` are fractional and must not be rounded to an integer;
      // totals such as `sessions` arrive whole and render whole either way.
      return compact ? formatCompact(value) : formatNumber(value, 1)
    default:
      return compact ? formatCompact(value) : formatNumber(value, 1)
  }
}

/**
 * Title-case a snake_case identifier for display: `form_factor` becomes `Form Factor`.
 *
 * Used for dimension names and segment keys, which travel as snake_case in query
 * parameters. Labels that read as prose — a tile's `label`, a filter's description — come
 * from the API already written and are never passed through here.
 */
export function humanize(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/**
 * Pluralise a noun against a count: `1 session`, `2 sessions`, `0 sessions`.
 *
 * Naive on purpose — it appends `s` unless given an explicit plural. English
 * irregulars in this domain are few and known (`country`/`countries`), so the caller
 * passes those in rather than this function carrying a dictionary it cannot complete.
 */
export function pluralize(count: number, singular: string, plural?: string): string {
  const noun = count === 1 ? singular : (plural ?? `${singular}s`)
  return `${formatNumber(count)} ${noun}`
}
