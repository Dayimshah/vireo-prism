import { useMemo, useState } from 'react'

import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { HeatmapMatrix } from '@/components/charts/HeatmapMatrix'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { exportRows, type CsvValue } from '@/lib/csv'
import { formatHours, formatNumber, formatPercent, humanize } from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * What a session looks like: when it happens, how deep it goes, how long it lasts.
 *
 * Six endpoints, and three of them return a shape that will produce a wrong number if it is
 * rendered as it arrives. Each is handled here rather than in the component it feeds, because
 * in every case the correct treatment depends on what the figure *means* — which is knowledge
 * this page has and a generic chart does not.
 *
 * 1. The heatmap returns nine rows per cell
 * ----------------------------------------
 * `/sessions/activity-heatmap` groups by `hour_local` as well as `weekday_utc` and `hour_utc`,
 * so the 168 cells of a week arrive as ~1900 rows — cell (Sunday, 00:00) alone comes back as
 * ten rows carrying 2, 2, 4, 2, 5, 5, 1, 59, 6 and 34 sessions. The SQL does this deliberately
 * (its own comment says both hour columns exist "so the dashboard can offer either view"), and
 * {@link HeatmapMatrix} documents that de-duplicating is the caller's job. So it is done in
 * {@link aggregateHeat} below, where the per-column rules can be stated:
 *
 * * `sessions` and `watch_seconds` are sums.
 * * `avg_duration_minutes` is a **session-weighted** mean. Averaging the ten parts of
 *   (Sunday, 00:00) unweighted gives 104.0 minutes; weighting by session count gives 90.8,
 *   and only the second is the mean duration of those 120 sessions. The unweighted figure
 *   lets a 2-session bucket outvote a 59-session one.
 * * `unique_users` is dropped, and this is the interesting case. It is a `COUNT(DISTINCT
 *   user_id)` evaluated inside each `hour_local` partition, so the parts do not add: a user in
 *   India, whose offset is +5:30, contributes sessions to two different local hours within one
 *   UTC hour and is counted in both. Summing the ten parts of (Sunday, 00:00) yields 76 for a
 *   cell of 120 sessions — an upper bound presented as a count. The true distinct total is not
 *   recoverable from this response at any level of effort, so the column is not shown. An
 *   upper bound labelled "users" is worse than an absent one.
 *
 * 2. Duration percentiles put the total in the middle
 * -------------------------------------------------
 * `/sessions/duration-percentiles` returns five `form_factor` rows *followed by* the `overall`
 * row, ordered by session count. The headline figure is therefore not `rows[0]`, and selecting
 * it by index reads "phone" as "all devices" — a plausible number, wrong by a few percent, with
 * nothing on screen to contradict it. It is selected by `dimension_type === 'overall'`.
 *
 * 3. Device-switching percentages are normalised within their group
 * ---------------------------------------------------------------
 * `row_type` splits the response into `breadth` (how many devices a user uses) and `transition`
 * (which device follows which). `pct_within_type` sums to 100 **within each**, so a single
 * table of all 24 rows shows percentages totalling 200. They are rendered as two groups.
 */

/** `EXTRACT(DOW)` is 0 for Sunday. `ISODOW` would be 1 for Monday — this query uses `DOW`. */
const WEEKDAY_LABELS = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
] as const

/** Which hour column drives the heatmap's horizontal axis. */
type HourBasis = 'utc' | 'local'

/** One de-duplicated heatmap cell. */
interface HeatCell extends Record<string, CsvValue> {
  weekday: number
  weekday_label: string
  hour: number
  sessions: number
  watch_hours: number
  mean_duration_minutes: number
}

/**
 * Collapse the raw rows into one per (weekday, hour) cell.
 *
 * `basis` picks which hour column is the axis; the other one is what gets summed away. See the
 * module docstring for why each column is treated the way it is.
 */
function aggregateHeat(
  rows: readonly {
    weekday_utc: number
    hour_utc: number
    hour_local: number
    sessions: number
    avg_duration_minutes: number
    watch_seconds: number
  }[],
  basis: HourBasis,
): HeatCell[] {
  interface Accumulator {
    weekday: number
    hour: number
    sessions: number
    watchSeconds: number
    /** Σ(mean × n), divided by Σn at the end. */
    durationTimesSessions: number
  }

  const byCell = new Map<string, Accumulator>()

  for (const row of rows) {
    const hour = basis === 'utc' ? row.hour_utc : row.hour_local
    const key = `${row.weekday_utc}:${hour}`

    let cell = byCell.get(key)
    if (!cell) {
      cell = {
        weekday: row.weekday_utc,
        hour,
        sessions: 0,
        watchSeconds: 0,
        durationTimesSessions: 0,
      }
      byCell.set(key, cell)
    }

    cell.sessions += row.sessions
    cell.watchSeconds += row.watch_seconds
    cell.durationTimesSessions += row.avg_duration_minutes * row.sessions
  }

  return [...byCell.values()]
    // Sorted here rather than left to the response order, because `HeatmapMatrix` preserves
    // insertion order by design — it will not re-sort, so an out-of-order Tuesday stays out
    // of order on screen.
    .sort((a, b) => a.weekday - b.weekday || a.hour - b.hour)
    .map((cell) => ({
      weekday: cell.weekday,
      weekday_label: WEEKDAY_LABELS[cell.weekday] ?? String(cell.weekday),
      hour: cell.hour,
      sessions: cell.sessions,
      watch_hours: Math.round((cell.watchSeconds / 3600) * 10) / 10,
      mean_duration_minutes:
        // Guarded, though a cell with no sessions cannot exist: it would have to come from a
        // row the GROUP BY produced with COUNT(*) = 0, which SQL does not do.
        cell.sessions > 0
          ? Math.round((cell.durationTimesSessions / cell.sessions) * 10) / 10
          : 0,
    }))
}

export function SessionsPage() {
  const { window } = useFilters()
  const [hourBasis, setHourBasis] = useState<HourBasis>('local')

  const heatmap = usePanel('/sessions/activity-heatmap')
  const depth = usePanel('/sessions/depth')
  const eventsPer = usePanel('/sessions/events-per-session')
  const durations = usePanel('/sessions/duration-percentiles')
  const switching = usePanel('/sessions/device-switching')
  const entryExit = usePanel('/sessions/entry-exit-screens')

  const heat = useMemo(
    () => aggregateHeat(heatmap.rows, hourBasis),
    [heatmap.rows, hourBasis],
  )

  // Selected by name, never by position — the `overall` row is last, not first.
  const overall = durations.rows.find((row) => row.dimension_type === 'overall')
  const byFormFactor = durations.rows.filter((row) => row.dimension_type === 'form_factor')

  const breadth = switching.rows.filter((row) => row.row_type === 'breadth')
  const transitions = switching.rows.filter((row) => row.row_type === 'transition')

  const depthColumns: Column<(typeof depth.rows)[number]>[] = [
    {
      key: 'depth_label',
      header: 'Depth reached',
      value: (row) => row.depth_label,
      className: 'font-medium',
    },
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    {
      ...numericColumn('pct_of_sessions', 'Share', (row) => row.pct_of_sessions, 'percent'),
      render: (row) => formatPercent(row.pct_of_sessions),
    },
    {
      // Cumulative: the share of sessions that got *at least* this far. It falls from 100%
      // down the table while `pct_of_sessions` does not, and the two are easy to confuse
      // without both present.
      ...numericColumn(
        'pct_reaching_at_least',
        'Reached at least',
        (row) => row.pct_reaching_at_least,
        'percent',
      ),
      render: (row) => formatPercent(row.pct_reaching_at_least),
    },
    numericColumn('avg_events', 'Mean events', (row) => row.avg_events),
    numericColumn('avg_max_step', 'Mean furthest step', (row) => row.avg_max_step),
    numericColumn('avg_watch_minutes', 'Mean watch min', (row) => row.avg_watch_minutes),
  ]

  const durationColumns: Column<(typeof durations.rows)[number]>[] = [
    {
      key: 'dimension',
      header: 'Form factor',
      value: (row) => row.dimension,
      className: 'font-medium',
    },
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    numericColumn('mean_minutes', 'Mean', (row) => row.mean_minutes),
    numericColumn('p25_minutes', 'p25', (row) => row.p25_minutes),
    numericColumn('median_minutes', 'Median', (row) => row.median_minutes),
    numericColumn('p75_minutes', 'p75', (row) => row.p75_minutes),
    numericColumn('p90_minutes', 'p90', (row) => row.p90_minutes),
    numericColumn('p99_minutes', 'p99', (row) => row.p99_minutes),
    {
      ...numericColumn('watch_share_pct', 'Watching', (row) => row.watch_share_pct, 'percent'),
      render: (row) => formatPercent(row.watch_share_pct),
    },
  ]

  const switchingColumns = (
    header: string,
  ): Column<(typeof switching.rows)[number]>[] => [
    { key: 'label', header, value: (row) => row.label, className: 'font-medium' },
    numericColumn('observations', 'Observations', (row) => row.observations),
    numericColumn('users', 'Users', (row) => row.users, 'users'),
    {
      ...numericColumn('pct_within_type', 'Share of group', (row) => row.pct_within_type, 'percent'),
      render: (row) => formatPercent(row.pct_within_type),
    },
  ]

  const eventsPerColumns: Column<(typeof eventsPer.rows)[number]>[] = [
    { key: 'bucket', header: 'Events', value: (row) => row.bucket, className: 'font-medium' },
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    {
      ...numericColumn('pct_of_sessions', 'Share', (row) => row.pct_of_sessions, 'percent'),
      render: (row) => formatPercent(row.pct_of_sessions),
    },
    numericColumn('avg_watch_minutes', 'Mean watch min', (row) => row.avg_watch_minutes),
    {
      ...numericColumn(
        'pct_with_playback',
        'Reached playback',
        (row) => row.pct_with_playback,
        'percent',
      ),
      render: (row) => formatPercent(row.pct_with_playback),
    },
  ]

  const entryExitColumns: Column<(typeof entryExit.rows)[number]>[] = [
    {
      key: 'entry_screen',
      header: 'Entry',
      value: (row) => humanize(row.entry_screen),
      className: 'font-medium',
    },
    { key: 'exit_screen', header: 'Exit', value: (row) => humanize(row.exit_screen) },
    {
      key: 'exit_signal',
      header: 'Reading',
      value: (row) => row.exit_signal,
      render: (row) => <span className="capitalize">{row.exit_signal}</span>,
    },
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    {
      ...numericColumn('pct_of_all', 'Share of all', (row) => row.pct_of_all, 'percent'),
      render: (row) => formatPercent(row.pct_of_all),
    },
    {
      // Identical to `pct_of_all` on the current dataset, where every session enters at
      // `splash`. Carried anyway: the two diverge the moment a second entry screen exists,
      // and dropping it now would make this table quietly wrong later rather than now.
      ...numericColumn(
        'pct_of_entry_screen',
        'Share of entry',
        (row) => row.pct_of_entry_screen,
        'percent',
      ),
      render: (row) => formatPercent(row.pct_of_entry_screen),
    },
    numericColumn('mean_minutes', 'Mean min', (row) => row.mean_minutes),
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    numericColumn('first_sessions', 'First sessions', (row) => row.first_sessions, 'sessions'),
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="When people watch"
        definition="Sessions by weekday and hour. Colour is session volume across the whole matrix, so it is comparable in both directions rather than normalised per row."
        {...heatmap.boundary}
        isEmpty={heat.length === 0 && heatmap.boundary.isEmpty}
        actions={
          <Select value={hourBasis} onValueChange={(value) => setHourBasis(value as HourBasis)}>
            <SelectTrigger className="h-7 w-40 text-xs" aria-label="Hour basis">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="local" className="text-xs">
                Viewer local hour
              </SelectItem>
              <SelectItem value="utc" className="text-xs">
                UTC hour
              </SelectItem>
            </SelectContent>
          </Select>
        }
        onExport={() =>
          exportRows(`sessions-activity-heatmap-${hourBasis}`, heat, {
            window: window ?? undefined,
          })
        }
      >
        <div className="space-y-3">
          <HeatmapMatrix
            rows={heat}
            rowKey={(row) => row.weekday}
            columnKey={(row) => row.hour}
            value={(row) => row.sessions}
            unit="sessions"
            rowLabel="Weekday"
            columnLabel="Hour"
            formatRow={(key) => WEEKDAY_LABELS[Number(key)] ?? key}
            formatColumn={(key) => String(key).padStart(2, '0')}
            detail={(row) =>
              `${formatHours(row.watch_hours)} watched · ${formatNumber(row.mean_duration_minutes)} min mean`
            }
          />

          <p className="text-2xs text-muted-foreground">
            {hourBasis === 'local' ? (
              <>
                Hours are each viewer&apos;s local time, using their country&apos;s fixed UTC
                offset. This is the sharper view — sessions are generated in local evenings, so
                UTC smears the peak across the world&apos;s timezones. The weekday is still UTC:
                the API returns no local weekday, so a late-night session can sit under the
                previous day.
              </>
            ) : (
              <>
                Hours are UTC. The peak looks broad rather than sharp because 21:00 in Mumbai and
                21:00 in São Paulo are eight hours apart — that flattening is the data being
                honest about timezones, not a fault. Switch to local hours for the real shape.
              </>
            )}{' '}
            Distinct users per cell are not shown: the API counts them within each local hour, so
            they cannot be added across a cell without counting a user twice.
          </p>
        </div>
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Session depth"
          definition="How far a session got, as a funnel of five ordered stages. Share is the proportion stopping at that depth; reached-at-least is cumulative and falls from 100%."
          {...depth.boundary}
          onExport={() =>
            exportRows('sessions-depth', depth.rows, { window: window ?? undefined })
          }
        >
          <div className="space-y-4">
            <CategoryBarChart
              data={depth.rows}
              categoryKey="depth_label"
              unit="sessions"
              hideLegend
              categoryWidth={140}
              height={200}
              series={[{ key: 'sessions', label: 'Sessions' }]}
            />
            <DataTable
              rows={depth.rows}
              columns={depthColumns}
              rowKey={(row) => String(row.depth_level)}
            />
          </div>
        </ChartCard>

        <ChartCard
          title="Events per session"
          definition="Sessions bucketed by event count, with how many of each bucket reached playback. A two-event session is a bounce: opened and closed."
          {...eventsPer.boundary}
          onExport={() =>
            exportRows('sessions-events-per-session', eventsPer.rows, {
              window: window ?? undefined,
            })
          }
        >
          <div className="space-y-4">
            <CategoryBarChart
              data={eventsPer.rows}
              categoryKey="bucket"
              unit="sessions"
              hideLegend
              categoryWidth={90}
              height={200}
              series={[{ key: 'sessions', label: 'Sessions' }]}
            />
            <DataTable
              rows={eventsPer.rows}
              columns={eventsPerColumns}
              rowKey={(row) => String(row.bucket_order)}
            />
          </div>
        </ChartCard>
      </div>

      <ChartCard
        title="Session length by form factor"
        definition="Duration percentiles per device class, and for all devices together. The p25 sits far below the median throughout, which is the short-bounce population showing up as a floor rather than as a tail."
        {...durations.boundary}
        onExport={() =>
          exportRows('sessions-duration-percentiles', durations.rows, {
            window: window ?? undefined,
          })
        }
      >
        <div className="space-y-3">
          {overall && (
            // Read by name from the response, not taken from a row position: this endpoint
            // orders by session count, so `overall` arrives last.
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-md bg-muted/40 px-3 py-2 text-xs">
              <span className="font-medium">{overall.dimension}</span>
              <span className="text-muted-foreground">
                {formatNumber(overall.sessions)} sessions
              </span>
              <span className="text-muted-foreground">
                median {formatNumber(overall.median_minutes)} min
              </span>
              <span className="text-muted-foreground">
                mean {formatNumber(overall.mean_minutes)} min
              </span>
              <span className="text-muted-foreground">
                p90 {formatNumber(overall.p90_minutes)} min
              </span>
              <span className="text-muted-foreground">
                {formatPercent(overall.watch_share_pct)} of that time watching
              </span>
            </div>
          )}

          <DataTable
            rows={byFormFactor}
            columns={durationColumns}
            rowKey={(row) => `${row.dimension_type}-${row.dimension}`}
          />
        </div>
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Device switching"
          definition="How many devices a user watches on, and which device follows which. The two groups are counted separately: each set of shares sums to 100% on its own."
          {...switching.boundary}
          onExport={() =>
            exportRows('sessions-device-switching', switching.rows, {
              window: window ?? undefined,
            })
          }
        >
          {/* Two tables rather than one. `pct_within_type` is normalised inside each group, so
              a combined table would show shares adding to 200%. */}
          <div className="space-y-4">
            <div className="space-y-1.5">
              <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                Devices per user
              </p>
              <DataTable
                rows={breadth}
                columns={switchingColumns('Breadth')}
                rowKey={(row) => `breadth-${row.label}`}
              />
            </div>

            <div className="space-y-1.5">
              <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                Consecutive-session transitions
              </p>
              <DataTable
                rows={transitions}
                columns={switchingColumns('Transition')}
                rowKey={(row) => `transition-${row.label}`}
                maxHeight="18rem"
              />
            </div>
          </div>
        </ChartCard>

        <ChartCard
          title="Where sessions end"
          definition="Entry and exit screen pairs. The exit signal is the API's reading of what an exit there means — a paywall exit is blocked, a search exit is an unfulfilled search."
          {...entryExit.boundary}
          onExport={() =>
            exportRows('sessions-entry-exit-screens', entryExit.rows, {
              window: window ?? undefined,
            })
          }
        >
          <div className="space-y-4">
            <CategoryBarChart
              data={entryExit.rows}
              categoryKey="exit_screen"
              unit="sessions"
              hideLegend
              categoryWidth={90}
              height={200}
              formatCategory={(value) => humanize(value)}
              series={[{ key: 'sessions', label: 'Sessions' }]}
            />
            <DataTable
              rows={entryExit.rows}
              columns={entryExitColumns}
              rowKey={(row) => `${row.entry_screen}-${row.exit_screen}`}
            />
          </div>
        </ChartCard>
      </div>
    </div>
  )
}
