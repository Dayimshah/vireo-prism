/**
 * CSV export.
 *
 * Every table and chart in this app is something a reader may want in a spreadsheet, and
 * the alternative to a working export is a screenshot retyped by hand.
 *
 * Two hazards, both handled here rather than at call sites
 * -------------------------------------------------------
 * **1. A `null` must export as an empty cell.** The string `"null"` in a spreadsheet
 * column is worse than useless — it makes the whole column text, so `AVERAGE()` silently
 * returns nothing. An empty cell is what every spreadsheet already understands as "no
 * value", and it preserves the meaning the API gave it: an undefined figure, not zero.
 *
 * **2. A cell beginning `=`, `+`, `-`, `@`, tab or CR is a formula injection risk.** Excel
 * and Sheets evaluate it on open, and `=HYPERLINK(...)` or a `cmd|` DDE payload in a
 * genre name would execute in the reader's spreadsheet, not here. This dataset is
 * synthetic, but the export path does not know that — a real deployment pointed at real
 * content titles would carry whatever a content editor typed. {@link escapeCell} prefixes
 * those with a tab, which spreadsheets treat as text without displaying the character.
 */

/** A value that can appear in an exported cell. */
export type CsvValue = string | number | boolean | null | undefined

/** One exported column: where the value comes from and what to call it. */
export interface CsvColumn<Row> {
  /** Header text. */
  header: string
  /** Pull the value out of a row. */
  value: (row: Row) => CsvValue
}

/**
 * Characters that make a spreadsheet treat a cell as a formula.
 *
 * A leading `-` is included even though it is usually a negative number, because a
 * *quoted* leading minus is how `-2+3+cmd|...` injections start. Numbers never reach this
 * check — {@link escapeCell} handles them before it, so genuine negatives are unaffected.
 */
const FORMULA_PREFIXES = ['=', '+', '-', '@', '\t', '\r']

/**
 * Render one value as a CSV field.
 *
 * Quoting follows RFC 4180: a field is wrapped in double quotes if it contains a comma,
 * quote, or newline, and an embedded quote is doubled.
 */
export function escapeCell(value: CsvValue): string {
  // The null rule. See the module docstring — this is the whole reason this function
  // takes `null | undefined` rather than requiring callers to pre-stringify.
  if (value === null || value === undefined) return ''

  if (typeof value === 'number') {
    // NaN and Infinity have no spreadsheet representation; an empty cell is honest.
    if (!Number.isFinite(value)) return ''
    // Unformatted on purpose: an exported figure should be the raw number, so the reader
    // can compute with it. Thousands separators would make it text.
    return String(value)
  }

  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE'

  let text = value

  if (FORMULA_PREFIXES.some((prefix) => text.startsWith(prefix))) {
    text = `\t${text}`
  }

  if (/[",\n\r\t]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`
  }

  return text
}

/**
 * Build a CSV document from rows and a column spec.
 *
 * CRLF line endings, per RFC 4180. Excel handles bare LF inconsistently across versions
 * and platforms, and this is the format every tool accepts.
 */
export function toCsv<Row>(rows: readonly Row[], columns: readonly CsvColumn<Row>[]): string {
  const header = columns.map((column) => escapeCell(column.header)).join(',')
  const body = rows.map((row) => columns.map((column) => escapeCell(column.value(row))).join(','))
  return [header, ...body].join('\r\n')
}

/**
 * Derive columns from the keys of the first row.
 *
 * For a table whose shape is only known at runtime. Header text is the raw column name
 * rather than a humanised one, because an export is read by a machine at least as often as
 * by a person and `watch_minutes_per_user` matches the API documentation.
 *
 * Returns `[]` for empty input — with no rows there are no keys, and inventing headers
 * would export a file that misdescribes what was asked for.
 */
export function inferColumns<Row extends Record<string, CsvValue>>(
  rows: readonly Row[],
): CsvColumn<Row>[] {
  const first = rows[0]
  if (!first) return []
  return Object.keys(first).map((key) => ({
    header: key,
    value: (row: Row) => row[key] ?? null,
  }))
}

/**
 * Prompt the browser to save a CSV file.
 *
 * The BOM is required, not decorative: without it Excel on Windows decodes the file as the
 * system codepage, and every non-ASCII character in a country or title name is mangled.
 * The dataset includes names outside ASCII, so this is reproducible rather than
 * theoretical. Every other tool treats the BOM as an encoding declaration and hides it.
 */
export function downloadCsv(filename: string, content: string): void {
  // The BOM is written as the `\uFEFF` escape rather than as a literal character. A raw
  // U+FEFF in source is invisible, so an editor normalising the file — or a copy-paste
  // through a tool that strips it — would silently remove the one byte Excel needs, and
  // produce mangled names with no visible diff to explain them. An escape cannot be lost
  // that way, and ESLint's `no-irregular-whitespace` rule flags the literal form.
  const blob = new Blob([`\uFEFF${content}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)

  const link = document.createElement('a')
  link.href = url
  link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`

  // Must be in the document for the click to be honoured in Firefox. Chromium allows a
  // detached element; Firefox silently does nothing.
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)

  // The blob is held in memory until revoked. Deferred rather than immediate: revoking in
  // the same tick can cancel the download before the browser has read the blob.
  setTimeout(() => {
    URL.revokeObjectURL(url)
  }, 1000)
}

/**
 * Build a filename that says what the data is and which window it covers.
 *
 * `prism-kpi-dau-2026-05-01-to-2026-07-31.csv`. The window is in the name because a
 * folder of exports whose only difference is the reporting period is otherwise
 * indistinguishable, and that is the normal way these accumulate.
 */
export function csvFilename(slug: string, window?: { date_from: string; date_to: string }): string {
  const safe = slug.replace(/[^a-z0-9]+/gi, '-').replace(/^-+|-+$/g, '').toLowerCase()
  const suffix = window ? `-${window.date_from}-to-${window.date_to}` : ''
  return `prism-${safe}${suffix}.csv`
}

/**
 * Export rows in one call: build the document, then save it.
 *
 * Columns are inferred when not supplied. Exporting an empty row set is a no-op rather
 * than a header-only file — a reader who clicked export on an empty chart is better served
 * by nothing happening than by a file that looks like data loss.
 */
export function exportRows<Row extends Record<string, CsvValue>>(
  slug: string,
  rows: readonly Row[],
  options: { columns?: readonly CsvColumn<Row>[]; window?: { date_from: string; date_to: string } } = {},
): boolean {
  if (rows.length === 0) return false
  const columns = options.columns ?? inferColumns(rows)
  if (columns.length === 0) return false
  downloadCsv(csvFilename(slug, options.window), toCsv(rows, columns))
  return true
}
