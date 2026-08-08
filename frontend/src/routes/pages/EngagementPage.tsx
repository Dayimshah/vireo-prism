import { useMemo } from 'react'

import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { TimeSeriesChart } from '@/components/charts/TimeSeriesChart'
import { useFilters } from '@/state/filters'
import { exportRows, type CsvValue } from '@/lib/csv'
import { formatPercent, humanize } from '@/lib/format'

/**
 * Active users, stickiness, and how the event mix is composed.
 *
 * Seven endpoints. DAU, WAU and MAU are three separate routes returning one series each, and
 * they are plotted together rather than as three cards: the interesting quantity is the
 * spread between them, and three adjacent charts with three different y-scales hide it.
 *
 * Joining three series on `day` is safe here
 * -----------------------------------------
 * Each of the three builds its own date spine server-side and LEFT JOINs onto it, so all
 * three cover every day in the window — including days with no activity, which come back as
 * explicit zeros rather than as missing rows. A merge keyed on `day` therefore cannot
 * silently drop a day, and a day genuinely absent from one series stays `undefined`, which
 * Recharts draws as a gap rather than as zero.
 *
 * Stickiness is a ratio and can be undefined
 * -----------------------------------------
 * `stickiness_pct` is DAU/MAU, and it arrives `null` when MAU is zero — an empty denominator,
 * not a stickiness of nothing. It is plotted on its own axis rather than beside the counts,
 * because a percentage and a user count share no scale.
 */

/**
 * One row of the merged DAU/WAU/MAU series.
 *
 * The index signature is `CsvValue`, not `unknown`. Every field on a merged row is a scalar,
 * so this is the honest type, and it is what lets the row go to `exportRows` and to the chart
 * from the same shape — `CsvValue` is a subtype of `unknown`, so the chart's looser
 * constraint is still satisfied.
 */
interface ActiveUsersDatum extends Record<string, CsvValue> {
  day: string
  dau?: number
  wau?: number
  mau?: number
}

export function EngagementPage() {
  const { window } = useFilters()

  const dau = usePanel('/kpi/dau')
  const wau = usePanel('/kpi/wau')
  const mau = usePanel('/kpi/mau')
  const stickiness = usePanel('/kpi/stickiness')
  const newVsReturning = usePanel('/kpi/new-vs-returning')
  const sessionsPerUser = usePanel('/kpi/sessions-per-user')
  const events = usePanel('/events/distribution')

  // Merged on `day`. A Map keyed by date rather than an index-wise zip: the three series are
  // separate requests that can complete in any order, and one arriving with a different row
  // count would otherwise pair the wrong figures together.
  const activeUsers = useMemo<ActiveUsersDatum[]>(() => {
    const byDay = new Map<string, ActiveUsersDatum>()

    const put = (day: string): ActiveUsersDatum => {
      let entry = byDay.get(day)
      if (!entry) {
        entry = { day }
        byDay.set(day, entry)
      }
      return entry
    }

    for (const row of dau.rows) put(row.day).dau = row.dau
    for (const row of wau.rows) put(row.day).wau = row.wau
    for (const row of mau.rows) put(row.day).mau = row.mau

    return [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day))
  }, [dau.rows, wau.rows, mau.rows])

  const eventColumns: Column<(typeof events.rows)[number]>[] = [
    {
      key: 'event_name',
      header: 'Event',
      value: (row) => row.event_name,
      className: 'font-medium',
    },
    { key: 'event_category', header: 'Category', value: (row) => humanize(row.event_category) },
    numericColumn('events', 'Events', (row) => row.events),
    numericColumn('users', 'Users', (row) => row.users, 'users'),
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    {
      ...numericColumn('pct_of_events', 'Share of events', (row) => row.pct_of_events, 'percent'),
      render: (row) => formatPercent(row.pct_of_events),
    },
    numericColumn('events_per_session', 'Per session', (row) => row.events_per_session),
    {
      ...numericColumn(
        'pct_of_sessions_reached',
        'Sessions reaching',
        (row) => row.pct_of_sessions_reached,
        'percent',
      ),
      render: (row) => formatPercent(row.pct_of_sessions_reached),
    },
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="Active users"
        definition="DAU, WAU and MAU on one axis. The gap between them is the quantity of interest: a WAU close to its MAU means most monthly users return weekly."
        // Pending until all three have arrived, so the chart does not appear with one series
        // and grow two more as the requests land.
        isPending={dau.boundary.isPending || wau.boundary.isPending || mau.boundary.isPending}
        error={dau.boundary.error ?? wau.boundary.error ?? mau.boundary.error}
        isWaiting={dau.boundary.isWaiting}
        isEmpty={activeUsers.length === 0 && dau.boundary.isEmpty}
        hasFilters={dau.boundary.hasFilters}
        hasLanguageFilter={dau.boundary.hasLanguageFilter}
        onRetry={dau.boundary.onRetry}
        info={dau.info}
        onExport={() => exportRows('kpi-active-users', activeUsers, { window: window ?? undefined })}
      >
        <TimeSeriesChart
          data={activeUsers}
          xKey="day"
          unit="users"
          series={[
            { key: 'dau', label: 'Daily' },
            { key: 'wau', label: 'Weekly' },
            { key: 'mau', label: 'Monthly' },
          ]}
        />
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Stickiness"
          definition="DAU as a percentage of MAU — how much of the monthly audience shows up on an average day. Undefined rather than zero when MAU is zero."
          {...stickiness.boundary}
          onExport={() =>
            exportRows('kpi-stickiness', stickiness.rows, { window: window ?? undefined })
          }
        >
          <TimeSeriesChart
            data={stickiness.rows}
            xKey="day"
            unit="percent"
            hideLegend
            series={[{ key: 'stickiness_pct', label: 'DAU/MAU' }]}
          />
        </ChartCard>

        <ChartCard
          title="New, returning and resurrected"
          definition="Active users each day split by their relationship to the product. The three sum to total_active, so they are stacked."
          {...newVsReturning.boundary}
          onExport={() =>
            exportRows('kpi-new-vs-returning', newVsReturning.rows, {
              window: window ?? undefined,
            })
          }
        >
          {/* Stacked, and legitimately so: the API returns `total_active` as the sum of these
              three, which is the one condition under which stacking is not misleading. */}
          <TimeSeriesChart
            data={newVsReturning.rows}
            xKey="day"
            variant="area"
            stacked
            unit="users"
            series={[
              { key: 'new_users', label: 'New' },
              { key: 'returning_users', label: 'Returning' },
              { key: 'resurrected_users', label: 'Resurrected' },
            ]}
          />
        </ChartCard>
      </div>

      <ChartCard
        title="Sessions per user"
        definition="Mean, median and 90th percentile sessions per active user each day. The mean sits above the median throughout, which is the signature of a long tail of heavy users."
        {...sessionsPerUser.boundary}
        onExport={() =>
          exportRows('kpi-sessions-per-user', sessionsPerUser.rows, {
            window: window ?? undefined,
          })
        }
      >
        <TimeSeriesChart
          data={sessionsPerUser.rows}
          xKey="day"
          series={[
            { key: 'mean_sessions_per_user', label: 'Mean' },
            { key: 'median_sessions_per_user', label: 'Median' },
            { key: 'p90_sessions_per_user', label: '90th percentile' },
          ]}
        />
      </ChartCard>

      <ChartCard
        title="Event mix"
        definition="Every event type in the window by volume, with how many sessions reach it. The heaviest query in the API — it scans the partitioned event table rather than a materialized view."
        {...events.boundary}
        onExport={() =>
          exportRows(
            'events-distribution',
            // `screen_mix` is a JSON object and the only non-scalar column in the API. It is
            // dropped from the export rather than stringified: a cell containing `{"player":
            // 143988}` is not something a spreadsheet can compute with, and the per-screen
            // breakdown is a different table than this one.
            events.rows.map(({ screen_mix: _screenMix, ...rest }) => rest),
            { window: window ?? undefined },
          )
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={events.rows}
            categoryKey="event_name"
            unit="count"
            hideLegend
            height={Math.max(200, events.rows.length * 22)}
            categoryWidth={150}
            series={[{ key: 'events', label: 'Events' }]}
          />

          <DataTable
            rows={events.rows}
            columns={eventColumns}
            rowKey={(row) => row.event_name}
            maxHeight="22rem"
          />
        </div>
      </ChartCard>
    </div>
  )
}

