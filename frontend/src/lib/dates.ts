/**
 * Date handling for reporting windows.
 *
 * Everything here operates on `YYYY-MM-DD` strings, which is what the API accepts and
 * returns. `Date` objects are used only inside a function, never passed across one.
 *
 * Why strings, and why every conversion is UTC
 * --------------------------------------------
 * JavaScript's two `Date` constructors disagree about timezone, and the disagreement is
 * silent:
 *
 * ```
 * new Date('2026-05-01')        // ISO date-only -> UTC midnight
 * new Date(2026, 4, 1)         // component form -> LOCAL midnight
 * ```
 *
 * So `new Date('2026-05-01').getDate()` returns **30** in any timezone west of Greenwich —
 * it is 30 April locally at that instant. A window built by parsing an API date and
 * formatting it back through local getters therefore loses a day for readers in the
 * Americas and gains nothing anywhere. The dataset here runs to 2026-08-06, so an
 * off-by-one at the boundary silently drops the last day of data.
 *
 * The rule this module follows: parse with `Date.UTC`, read with `getUTC*`, and never let
 * a local-time getter touch a reporting date. `formatMonthLabel` is the one function that
 * produces human text, and it passes `timeZone: 'UTC'` explicitly for the same reason.
 *
 * The dataset is not "now"
 * ------------------------
 * Seeded data ends on a fixed date — 2026-08-06 in the current dataset — and the API
 * requires an explicit window with no default, precisely so a "last 30 days" preset cannot
 * open every chart empty. Presets here are therefore computed from
 * `/meta/bounds`, never from `Date.now()`. {@link clampWindow} exists so a hand-edited URL
 * cannot ask for a window outside the data.
 */

/** A date as the API exchanges it: `YYYY-MM-DD`. */
export type IsoDate = string

/** An inclusive reporting window. */
export interface DateWindow {
  /** First day included. */
  date_from: IsoDate
  /** Last day included. */
  date_to: IsoDate
}

/** Milliseconds in a day. Safe as a constant only because all arithmetic here is UTC. */
const MS_PER_DAY = 86_400_000

/** Matches `YYYY-MM-DD` and nothing else. */
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * Parse `YYYY-MM-DD` into a UTC timestamp, or `null` if it is not a real date.
 *
 * Rejects both malformed strings and impossible ones. `2026-02-30` matches the pattern and
 * `Date.UTC` silently rolls it forward to 2 March, so the round-trip comparison at the end
 * is what actually validates the day — without it, a typo in a URL would become a
 * plausible-looking window a day or two from where the reader thought they were.
 */
export function parseIsoDate(value: string | null | undefined): number | null {
  if (!value) return null

  const match = ISO_DATE_PATTERN.exec(value.trim())
  if (!match) return null

  const [, year, month, day] = match
  // `match` is a successful exec of a fully-parenthesised pattern, so the groups exist;
  // `noUncheckedIndexedAccess` cannot know that, and Number(undefined) would be NaN.
  const y = Number(year)
  const m = Number(month)
  const d = Number(day)

  if (m < 1 || m > 12 || d < 1 || d > 31) return null

  const timestamp = Date.UTC(y, m - 1, d)
  if (Number.isNaN(timestamp)) return null

  // Reject a rolled-over date: Date.UTC(2026, 1, 30) is 2 March, which would otherwise
  // pass as a valid parse of "2026-02-30".
  const roundTrip = new Date(timestamp)
  if (
    roundTrip.getUTCFullYear() !== y ||
    roundTrip.getUTCMonth() !== m - 1 ||
    roundTrip.getUTCDate() !== d
  ) {
    return null
  }

  return timestamp
}

/**
 * True when a string is a well-formed, real `YYYY-MM-DD` date.
 *
 * Declared as a type predicate rather than returning a plain `boolean`, so a caller that
 * checks a nullable value gets it narrowed to `string`. Without the predicate, reading a
 * date out of the URL and validating it still leaves `string | null`, and every call site
 * needs a cast that asserts what the check already established.
 */
export function isValidIsoDate(value: string | null | undefined): value is IsoDate {
  return parseIsoDate(value) !== null
}

/** Format a UTC timestamp back to `YYYY-MM-DD`. */
export function toIsoDate(timestamp: number): IsoDate {
  // `toISOString` is always UTC, which is the invariant this module maintains. Slicing the
  // date portion is exact rather than a heuristic: the format is fixed by the spec.
  return new Date(timestamp).toISOString().slice(0, 10)
}

/**
 * Add days to a date. Negative subtracts.
 *
 * Returns the input unchanged if it does not parse, so a caller chaining these cannot
 * turn a bad date into `NaN-NaN-NaN`.
 */
export function addDays(date: IsoDate, days: number): IsoDate {
  const timestamp = parseIsoDate(date)
  if (timestamp === null) return date
  return toIsoDate(timestamp + days * MS_PER_DAY)
}

/**
 * Count days in an inclusive window, matching the API's `meta.window.days`.
 *
 * Both endpoints count, so a single-day window is 1 rather than 0. Returns `null` if
 * either end is unparseable — a caller showing "— days" is better than one showing "NaN".
 */
export function windowLength(window: DateWindow): number | null {
  const from = parseIsoDate(window.date_from)
  const to = parseIsoDate(window.date_to)
  if (from === null || to === null) return null
  return Math.round((to - from) / MS_PER_DAY) + 1
}

/** True when `from` is not after `to` — the ordering the API enforces with a 422. */
export function isOrderedWindow(window: DateWindow): boolean {
  const from = parseIsoDate(window.date_from)
  const to = parseIsoDate(window.date_to)
  if (from === null || to === null) return false
  return from <= to
}

/**
 * The dataset's own boundaries, as `/meta/bounds` reports them.
 *
 * Declared locally rather than imported from the generated schema: this module is pure
 * date arithmetic with no API dependency, and the two fields it needs are stable.
 */
export interface Bounds {
  first_activity_date: IsoDate
  last_activity_date: IsoDate
}

/**
 * Pull a window back inside the dataset, preserving its length where possible.
 *
 * Called on every window that arrives from the URL, because a reader can edit it and a
 * bookmark can outlive a reseed. Sliding rather than truncating is deliberate: a reader who
 * asked for 90 days and typed a start date a year before the data begins wants 90 days of
 * data, not a 400-day window silently clipped at both ends.
 *
 * Order of operations matters. The length is measured first, then the window is slid, and
 * only then is it clipped — clipping first would shorten it before the slide had a chance
 * to preserve the length. A window longer than the entire dataset ends up spanning it
 * exactly, which is the most anyone can ask for.
 */
export function clampWindow(window: DateWindow, bounds: Bounds | null | undefined): DateWindow {
  if (!bounds) return window

  const first = parseIsoDate(bounds.first_activity_date)
  const last = parseIsoDate(bounds.last_activity_date)
  let from = parseIsoDate(window.date_from)
  let to = parseIsoDate(window.date_to)

  // Nothing to clamp against, or nothing coherent to clamp.
  if (first === null || last === null || from === null || to === null) return window
  if (first > last) return window

  // A reversed window is a caller error the API rejects with a 422. Swapping it here would
  // hide that, so it is returned untouched and the request fails visibly.
  if (from > to) return window

  const requestedDays = Math.round((to - from) / MS_PER_DAY) + 1
  const availableDays = Math.round((last - first) / MS_PER_DAY) + 1

  if (requestedDays >= availableDays) {
    return { date_from: toIsoDate(first), date_to: toIsoDate(last) }
  }

  if (from < first) {
    from = first
    to = from + (requestedDays - 1) * MS_PER_DAY
  }

  if (to > last) {
    to = last
    from = to - (requestedDays - 1) * MS_PER_DAY
  }

  // The slide above can push the other end out when the window is nearly as wide as the
  // dataset; a final clip is cheap and makes the result unconditionally in range.
  if (from < first) from = first
  if (to > last) to = last

  return { date_from: toIsoDate(from), date_to: toIsoDate(to) }
}

/** A named window a reader can pick from the topbar. */
export interface WindowPreset {
  id: string
  label: string
  /** Days the preset spans, or `null` for the whole dataset. */
  days: number | null
}

/**
 * The preset windows offered in the UI.
 *
 * Anchored to the dataset's last activity date, not to today — see the module docstring.
 * "Last 30 days" therefore means the 30 days ending 2026-08-06 in the current dataset, and
 * a reader opening the app sees data rather than an empty chart.
 */
export const WINDOW_PRESETS: readonly WindowPreset[] = [
  { id: '7d', label: 'Last 7 days', days: 7 },
  { id: '30d', label: 'Last 30 days', days: 30 },
  { id: '90d', label: 'Last 90 days', days: 90 },
  { id: '180d', label: 'Last 180 days', days: 180 },
  { id: '365d', label: 'Last 365 days', days: 365 },
  { id: 'all', label: 'All time', days: null },
] as const

/**
 * Resolve a preset against the dataset's bounds.
 *
 * Ends at `last_activity_date` and counts backwards. Both endpoints are inclusive, so a
 * 30-day window ends on the last day and starts 29 days earlier — the off-by-one that
 * `windowLength` mirrors.
 */
export function resolvePreset(preset: WindowPreset, bounds: Bounds): DateWindow {
  const first = parseIsoDate(bounds.first_activity_date)
  const last = parseIsoDate(bounds.last_activity_date)

  if (first === null || last === null) {
    return { date_from: bounds.first_activity_date, date_to: bounds.last_activity_date }
  }

  if (preset.days === null) {
    return { date_from: toIsoDate(first), date_to: toIsoDate(last) }
  }

  const from = Math.max(first, last - (preset.days - 1) * MS_PER_DAY)
  return { date_from: toIsoDate(from), date_to: toIsoDate(last) }
}

/**
 * The default window for a first visit: 90 days, or the whole dataset if it is shorter.
 *
 * 90 days rather than 30 because several dashboards are cohort- and retention-shaped, and
 * a 30-day window leaves most cohort cells unelapsed and therefore `null` — a first
 * impression of a mostly-blank matrix that reads as broken rather than as young.
 */
export function defaultWindow(bounds: Bounds): DateWindow {
  const preset = WINDOW_PRESETS.find((p) => p.id === '90d')
  // The find cannot fail against the literal above; the fallback keeps this total rather
  // than asserting non-null.
  return resolvePreset(preset ?? { id: '90d', label: 'Last 90 days', days: 90 }, bounds)
}

/**
 * Identify which preset a window corresponds to, or `null` if it is custom.
 *
 * Lets the topbar highlight the active preset after a window arrives from the URL. Matched
 * on both length and end date: a 30-day window ending mid-dataset is not "Last 30 days",
 * and highlighting it as such would misdescribe what the charts show.
 */
export function matchPreset(window: DateWindow, bounds: Bounds | null): string | null {
  if (!bounds) return null

  const length = windowLength(window)
  if (length === null) return null

  const last = parseIsoDate(bounds.last_activity_date)
  const to = parseIsoDate(window.date_to)
  if (last === null || to === null || to !== last) return null

  const first = parseIsoDate(bounds.first_activity_date)
  const from = parseIsoDate(window.date_from)
  if (first !== null && from !== null && from === first) return 'all'

  return WINDOW_PRESETS.find((preset) => preset.days === length)?.id ?? null
}

/**
 * The equal-length period immediately before a window.
 *
 * Mirrors what `/overview` computes server-side for its deltas, so a page can label a
 * comparison consistently with the tiles. The API returns its own `comparison_window`, and
 * that value is authoritative wherever it is present — this is for pages that compare
 * without calling the overview endpoint.
 */
export function previousWindow(window: DateWindow): DateWindow {
  const length = windowLength(window)
  const from = parseIsoDate(window.date_from)
  if (length === null || from === null) return window

  const previousTo = from - MS_PER_DAY
  const previousFrom = previousTo - (length - 1) * MS_PER_DAY
  return { date_from: toIsoDate(previousFrom), date_to: toIsoDate(previousTo) }
}

/** Cache for the two `Intl.DateTimeFormat` instances this module uses. */
const dateFormatters = new Map<string, Intl.DateTimeFormat>()

function dateFormatter(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = JSON.stringify(options)
  let cached = dateFormatters.get(key)
  if (!cached) {
    // `timeZone: 'UTC'` on every instance. Without it, formatting a UTC-parsed date with a
    // local-timezone formatter reintroduces exactly the off-by-one this module exists to
    // avoid — and it would appear only for readers in negative-offset timezones.
    cached = new Intl.DateTimeFormat('en-US', { ...options, timeZone: 'UTC' })
    dateFormatters.set(key, cached)
  }
  return cached
}

/** Format a date for display: `1 May 2026`. */
export function formatDate(date: IsoDate | null | undefined): string {
  const timestamp = parseIsoDate(date)
  if (timestamp === null) return '—'
  return dateFormatter({ day: 'numeric', month: 'short', year: 'numeric' }).format(timestamp)
}

/** Format a date without its year, for axis ticks inside a single-year window: `1 May`. */
export function formatDateShort(date: IsoDate | null | undefined): string {
  const timestamp = parseIsoDate(date)
  if (timestamp === null) return '—'
  return dateFormatter({ day: 'numeric', month: 'short' }).format(timestamp)
}

/**
 * Format a month bucket: `Jul 2026`.
 *
 * Monthly series in this API return the first day of the month — `revenue_month:
 * "2026-07-01"` — so rendering them with {@link formatDate} would print a spurious `1`.
 */
export function formatMonthLabel(date: IsoDate | null | undefined): string {
  const timestamp = parseIsoDate(date)
  if (timestamp === null) return '—'
  return dateFormatter({ month: 'short', year: 'numeric' }).format(timestamp)
}

/** Format a window as a single readable range: `1 May 2026 – 31 Jul 2026`. */
export function formatWindow(window: DateWindow): string {
  // En dash with thin spaces: a hyphen next to numbers reads as a minus sign.
  return `${formatDate(window.date_from)} – ${formatDate(window.date_to)}`
}
