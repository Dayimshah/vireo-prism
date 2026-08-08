import { ArrowDown, ArrowUp } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { CsvValue } from '@/lib/csv'
import { EMPTY, formatByUnit, isAbsent } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * A sortable table over query rows, with CSV export.
 *
 * Several endpoints are leaderboards rather than series — the churn scorecard, country
 * ranking, top titles, RFM segments — and a chart of twenty-four columns is a table with
 * extra steps.
 *
 * A `null` sorts to the end in **both** directions
 * -----------------------------------------------
 * This is the one non-obvious rule here, and it follows from what `null` means in this API:
 * an undefined figure, not a small one. Treating it as `-Infinity` would put "we could not
 * compute a conversion rate" at the bottom of an ascending sort and at the top of a
 * descending one, in both cases claiming a rank the data does not support. Sorting absent
 * values last regardless of direction means the ranked column always reads as "these are the
 * rows where this figure exists, in order" followed by the rows where it does not.
 *
 * The raw value is what gets sorted and exported
 * ---------------------------------------------
 * {@link Column.value} returns the unformatted value and {@link Column.render} is only
 * consulted for display. Sorting on formatted text would order `1.1M` before `847` because
 * `'1'` precedes `'8'`, and exporting it would put thousands separators into cells a
 * spreadsheet then treats as text.
 */

/** One column: where its value comes from, and how to show it. */
export interface Column<Row> {
  /** Stable identity, used as the React key and the sort key. */
  key: string

  /** Header text. */
  header: string

  /** The raw value — sorted on, exported, and formatted when no `render` is given. */
  value: (row: Row) => CsvValue

  /**
   * Override the rendered cell.
   *
   * For a badge, a bar, or anything that is not a formatted scalar. Receives the row rather
   * than the value, so a cell can read a sibling column — a risk band that colours itself
   * from `risk_score`, for instance.
   */
  render?: (row: Row) => ReactNode

  /** Unit passed to {@link formatByUnit} when the value is numeric. */
  unit?: string

  /** Right-aligned by default for numbers, left for everything else. */
  align?: 'left' | 'right'

  /** Set `false` for a column with no meaningful order. */
  sortable?: boolean

  /** Extra classes on the cell — `font-medium` for a name column, a width hint. */
  className?: string
}

export interface DataTableProps<Row> {
  rows: readonly Row[]

  columns: readonly Column<Row>[]

  /** Stable React key per row. */
  rowKey: (row: Row, index: number) => string

  /**
   * Column to sort by on first render, and which way.
   *
   * Omit to preserve the order the API returned. That is the right default for anything
   * already ranked server-side — `watch_rank`, `loss_rank`, a funnel's `step_order` — where
   * re-sorting client-side would discard the ordering the query was written to produce.
   */
  initialSort?: { key: string; direction: 'asc' | 'desc' }

  /** Cap the body height and scroll inside it. For a long leaderboard. */
  maxHeight?: string

  className?: string
}

/**
 * Compare two raw cell values.
 *
 * Numbers numerically, booleans with `true` first, everything else as a locale-aware string
 * so `Ürümqi` files next to `U` rather than after `Z`.
 */
function compareValues(a: CsvValue, b: CsvValue): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  if (typeof a === 'boolean' && typeof b === 'boolean') return Number(b) - Number(a)
  return String(a).localeCompare(String(b))
}

/** True when a value must sort last whichever direction is active. */
function isSortAbsent(value: CsvValue): boolean {
  if (value === null || value === undefined || value === '') return true
  return typeof value === 'number' && isAbsent(value)
}

/** Default rendering for a raw value. */
function renderValue(value: CsvValue, unit: string | undefined): ReactNode {
  if (value === null || value === undefined) return EMPTY
  if (typeof value === 'number') return formatByUnit(value, unit)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return value
}

export function DataTable<Row>({
  rows,
  columns,
  rowKey,
  initialSort,
  maxHeight,
  className,
}: DataTableProps<Row>) {
  const [sort, setSort] = useState<{ key: string; direction: 'asc' | 'desc' } | null>(
    initialSort ?? null,
  )

  const sorted = useMemo(() => {
    if (!sort) return rows
    const column = columns.find((entry) => entry.key === sort.key)
    if (!column) return rows

    const factor = sort.direction === 'asc' ? 1 : -1

    // Copied before sorting: `rows` comes from react-query's cache, and sorting it in place
    // would mutate the cached array — every other consumer of that query would silently see
    // this table's ordering.
    return [...rows].sort((left, right) => {
      const a = column.value(left)
      const b = column.value(right)

      // Absent last in both directions — see the module docstring.
      const aAbsent = isSortAbsent(a)
      const bAbsent = isSortAbsent(b)
      if (aAbsent && bAbsent) return 0
      if (aAbsent) return 1
      if (bAbsent) return -1

      return compareValues(a, b) * factor
    })
  }, [rows, columns, sort])

  const toggleSort = (key: string) => {
    setSort((current) => {
      if (current?.key !== key) {
        // First click on a new column sorts descending. Every sortable column here is a
        // magnitude — watch hours, revenue, risk — and "largest first" is what a reader
        // asking to sort one of those means.
        return { key, direction: 'desc' }
      }
      return { key, direction: current.direction === 'desc' ? 'asc' : 'desc' }
    })
  }

  return (
    <div className={cn('w-full', className)}>
      {/* No export control here. Export is `ChartCard`'s header button, wired by the page to
          `exportRows` from `lib/csv` — one affordance in one place. The cost is that an
          export reflects the API's ordering rather than a reader's current sort; the
          alternative was two buttons doing nearly the same thing, one of them hidden. */}
      <div
        className={cn(maxHeight && 'overflow-y-auto scrollbar-thin')}
        style={maxHeight ? { maxHeight } : undefined}
      >
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              {columns.map((column) => {
                const isSorted = sort?.key === column.key
                const alignRight = column.align === 'right'
                const sortable = column.sortable !== false

                return (
                  <TableHead
                    key={column.key}
                    className={cn(alignRight && 'text-right', column.className)}
                    // Announced to a screen reader, so the current order is available
                    // without seeing the arrow.
                    aria-sort={
                      isSorted
                        ? sort.direction === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : undefined
                    }
                  >
                    {sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(column.key)}
                        className={cn(
                          'inline-flex items-center gap-1 rounded-sm transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                          alignRight && 'flex-row-reverse',
                          isSorted && 'text-foreground',
                        )}
                      >
                        {column.header}
                        {isSorted &&
                          (sort.direction === 'asc' ? (
                            <ArrowUp className="size-3" />
                          ) : (
                            <ArrowDown className="size-3" />
                          ))}
                      </button>
                    ) : (
                      column.header
                    )}
                  </TableHead>
                )
              })}
            </TableRow>
          </TableHeader>

          <TableBody>
            {sorted.map((row, index) => (
              <TableRow key={rowKey(row, index)}>
                {columns.map((column) => (
                  <TableCell
                    key={column.key}
                    className={cn(
                      'text-xs',
                      column.align === 'right' && 'text-right',
                      column.className,
                    )}
                  >
                    {column.render ? column.render(row) : renderValue(column.value(row), column.unit)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

/**
 * Build a right-aligned numeric column.
 *
 * A shorthand, because most columns in this app are figures and repeating
 * `align: 'right'` beside every one of them is noise that hides the interesting fields.
 */
export function numericColumn<Row>(
  key: string,
  header: string,
  value: (row: Row) => number | null | undefined,
  unit?: string,
): Column<Row> {
  return { key, header, value, unit, align: 'right' }
}
