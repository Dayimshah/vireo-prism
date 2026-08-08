import { useMemo, useState } from 'react'

import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { HeatmapMatrix } from '@/components/charts/HeatmapMatrix'
import { TimeSeriesChart } from '@/components/charts/TimeSeriesChart'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { exportRows, type CsvValue } from '@/lib/csv'
import { formatMonthLabel } from '@/lib/dates'
import { EMPTY, formatCurrency, formatNumber, formatPercent, isAbsent } from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * Cohorts: what happens to a group of users defined by when they arrived.
 *
 * Four endpoints, all of which take a `min_cohort_size` floor — and on the current dataset that
 * floor is the most consequential thing on the page.
 *
 * The floor is a control, not a constant
 * ------------------------------------
 * `min_cohort_size` defaults to 30 across all four endpoints, and the API explains why: *"A
 * three-user cohort retains at 0%, 33%, 67% or 100%, and whichever it lands on dominates the
 * heatmap."* That is correct reasoning. It also means that on this dataset — roughly 114 signups
 * across the window — two of the four panels return **nothing** at the default. The largest
 * weekly cohort holds 14 users; the largest acquisition channel holds 28. Both fall under 30.
 *
 * There were three ways to handle that and only one of them is honest. Hardcoding a lower floor
 * would fill the page by suppressing a statistical warning the API deliberately raises. Leaving
 * the default with a bare "no data" would look like a broken panel. So the floor is a control on
 * the page, initialised to the API's own default, with an empty state that names it as the
 * reason. A reader who lowers it has been told what they are trading away.
 *
 * `is_complete` is why a null here is not zero
 * -----------------------------------------
 * The matrices return `is_complete: false` with `active_users` and `retention_pct` both `null`
 * for any cell whose period has not finished. On the live window that is 21 of 26 monthly cells.
 * A cell reading `retention_pct: 0.0` with `is_complete: true` is a real, observed zero — nobody
 * came back — and it must not look like an unobserved one. {@link HeatmapMatrix} paints absent
 * cells with a grey outside the colour ramp for exactly this distinction, so the two read
 * differently without any work here.
 *
 * `observation_end` is deliberately not exposed
 * ------------------------------------------
 * It exists to hold the follow-up period equal across cohorts, so a younger cohort is not judged
 * on a shorter window. The matrices already solve that server-side by nulling incomplete cells —
 * a June cohort simply has no month-2 figure to be unfairly compared on. Adding a second,
 * overlapping mechanism for the same hazard would invite the two to disagree.
 */

/** Floors offered by the control. 30 is the API's default and stays first. */
const COHORT_SIZE_FLOORS = [30, 20, 10, 5, 1] as const

/** One row of the revenue series, pivoted to a column per cohort. */
interface RevenueDatum extends Record<string, CsvValue> {
  month_n: number
}

export function CohortsPage() {
  const { window } = useFilters()
  const [minCohortSize, setMinCohortSize] = useState<number>(COHORT_SIZE_FLOORS[0])

  const extra = { min_cohort_size: minCohortSize }

  const monthly = usePanel('/cohort/monthly-matrix', { extra })
  const weekly = usePanel('/cohort/weekly-matrix', { extra })
  const revenue = usePanel('/cohort/revenue-cumulative', { extra })
  const ltv = usePanel('/cohort/ltv-by-channel', { extra })

  /** Shared by every card, so the floor is adjusted in one place. */
  const floorControl = (
    <Select
      value={String(minCohortSize)}
      onValueChange={(value) => setMinCohortSize(Number(value))}
    >
      <SelectTrigger className="h-7 w-36 text-xs" aria-label="Minimum cohort size">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {COHORT_SIZE_FLOORS.map((size) => (
          <SelectItem key={size} value={String(size)} className="text-xs">
            {size === 30 ? '30+ users (default)' : `${size}+ users`}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )

  const floorEmptyMessage =
    `No cohort in this window reaches ${minCohortSize} users, so every one of them was filtered out. ` +
    `Lower the minimum cohort size to include smaller cohorts — their percentages move in large ` +
    `steps, which is the reason for the floor in the first place.`

  // Pivoted to one series per cohort. Keyed on `month_n`, not zipped: cohorts start at
  // different months — May's first revenue lands in month 1, June's in month 0 — so an
  // index-wise merge would align May's month 1 with June's month 0.
  const revenueSeries = useMemo(() => {
    const byMonth = new Map<number, RevenueDatum>()
    const cohorts = new Set<string>()

    for (const row of revenue.rows) {
      cohorts.add(row.cohort_month)
      let entry = byMonth.get(row.month_n)
      if (!entry) {
        entry = { month_n: row.month_n }
        byMonth.set(row.month_n, entry)
      }
      entry[row.cohort_month] = row.cumulative_arpu_usd
    }

    return {
      data: [...byMonth.values()].sort((a, b) => a.month_n - b.month_n),
      series: [...cohorts].sort().map((cohort) => ({
        key: cohort,
        label: formatMonthLabel(cohort),
      })),
    }
  }, [revenue.rows])

  const monthlyColumns: Column<(typeof monthly.rows)[number]>[] = [
    {
      key: 'cohort_month',
      header: 'Cohort',
      value: (row) => row.cohort_month,
      render: (row) => formatMonthLabel(row.cohort_month),
      className: 'font-medium',
    },
    { key: 'month_n', header: 'Month', value: (row) => row.month_n, align: 'right' },
    numericColumn('cohort_size', 'Cohort size', (row) => row.cohort_size, 'users'),
    {
      ...numericColumn('active_users', 'Active', (row) => row.active_users, 'users'),
      render: (row) => (isAbsent(row.active_users) ? EMPTY : formatNumber(row.active_users)),
    },
    {
      ...numericColumn('retention_pct', 'Retention', (row) => row.retention_pct, 'percent'),
      render: (row) => formatPercent(row.retention_pct),
    },
    {
      key: 'is_complete',
      header: 'Observed',
      value: (row) => row.is_complete,
      render: (row) =>
        row.is_complete ? (
          <span className="text-muted-foreground">yes</span>
        ) : (
          <Badge variant="secondary">not yet</Badge>
        ),
    },
  ]

  const ltvColumns: Column<(typeof ltv.rows)[number]>[] = [
    {
      key: 'channel',
      header: 'Channel',
      value: (row) => row.channel,
      className: 'font-medium',
      render: (row) => (
        <span className="flex items-center gap-1.5">
          {row.channel}
          {row.is_paid && (
            <Badge variant="secondary" className="shrink-0">
              paid
            </Badge>
          )}
        </span>
      ),
    },
    { key: 'channel_group', header: 'Group', value: (row) => row.channel_group },
    numericColumn('users_acquired', 'Acquired', (row) => row.users_acquired, 'users'),
    numericColumn('users_converted', 'Converted', (row) => row.users_converted, 'users'),
    {
      ...numericColumn('conversion_pct', 'Conversion', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    {
      ...numericColumn('cac_usd', 'CAC', (row) => row.cac_usd, 'usd'),
      render: (row) => formatCurrency(row.cac_usd),
    },
    {
      ...numericColumn(
        'ltv_per_acquired_usd',
        'LTV per acquired',
        (row) => row.ltv_per_acquired_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.ltv_per_acquired_usd),
    },
    {
      // `null` for an organic channel: the ratio's denominator is a CAC of zero, and a
      // division by nothing has no value. It is emphatically not "a ratio of 0".
      ...numericColumn('ltv_to_cac_ratio', 'LTV to CAC', (row) => row.ltv_to_cac_ratio),
      render: (row) =>
        isAbsent(row.ltv_to_cac_ratio) ? EMPTY : `${formatNumber(row.ltv_to_cac_ratio, 2)}×`,
    },
    {
      ...numericColumn(
        'revenue_per_payer_usd',
        'Revenue per payer',
        (row) => row.revenue_per_payer_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.revenue_per_payer_usd),
    },
    {
      ...numericColumn('total_spend_usd', 'Spend', (row) => row.total_spend_usd, 'usd'),
      render: (row) => formatCurrency(row.total_spend_usd),
    },
    {
      ...numericColumn(
        'net_contribution_usd',
        'Net contribution',
        (row) => row.net_contribution_usd,
        'usd',
      ),
      render: (row) => (
        <span className={row.net_contribution_usd < 0 ? 'text-destructive' : undefined}>
          {formatCurrency(row.net_contribution_usd)}
        </span>
      ),
    },
  ]

  const revenueColumns: Column<(typeof revenue.rows)[number]>[] = [
    {
      key: 'cohort_month',
      header: 'Cohort',
      value: (row) => row.cohort_month,
      render: (row) => formatMonthLabel(row.cohort_month),
      className: 'font-medium',
    },
    { key: 'month_n', header: 'Month', value: (row) => row.month_n, align: 'right' },
    numericColumn('cohort_size', 'Cohort size', (row) => row.cohort_size, 'users'),
    {
      ...numericColumn('revenue_usd', 'Revenue', (row) => row.revenue_usd, 'usd'),
      render: (row) => formatCurrency(row.revenue_usd),
    },
    {
      ...numericColumn(
        'cumulative_revenue_usd',
        'Cumulative',
        (row) => row.cumulative_revenue_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.cumulative_revenue_usd),
    },
    {
      ...numericColumn(
        'cumulative_arpu_usd',
        'Cumulative ARPU',
        (row) => row.cumulative_arpu_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.cumulative_arpu_usd),
    },
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="Monthly cohort retention"
        definition="Each signup month followed across its subsequent months. Grey cells are periods that have not finished yet — undefined, not zero. A dark cell reading 0% is a real observation that nobody returned."
        {...monthly.boundary}
        emptyMessage={floorEmptyMessage}
        actions={floorControl}
        onExport={() =>
          exportRows('cohort-monthly-matrix', monthly.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <HeatmapMatrix
            rows={monthly.rows}
            rowKey={(row) => row.cohort_month}
            columnKey={(row) => row.month_n}
            value={(row) => row.retention_pct}
            unit="percent"
            rowLabel="Cohort"
            columnLabel="Month"
            formatRow={(key) => formatMonthLabel(key)}
            detail={(row) => `${formatNumber(row.cohort_size)} users in cohort`}
          />

          <p className="text-2xs text-muted-foreground">
            Read the cohort size in each tooltip before reading its percentages. These cohorts hold
            around fifty users, so one person returning moves a cell by two points — the shape of
            the matrix is meaningful, the precision of any single cell is not.
          </p>

          <DataTable
            rows={monthly.rows}
            columns={monthlyColumns}
            rowKey={(row) => `${row.cohort_month}-${row.month_n}`}
            maxHeight="22rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Weekly cohort retention"
        definition="The same measure at weekly grain, which gives more cohorts and fewer users in each. Every weekly cohort in this dataset is small enough that the default floor excludes it."
        {...weekly.boundary}
        emptyMessage={floorEmptyMessage}
        actions={floorControl}
        onExport={() =>
          exportRows('cohort-weekly-matrix', weekly.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <HeatmapMatrix
            rows={weekly.rows}
            rowKey={(row) => row.cohort_week}
            columnKey={(row) => row.week_n}
            value={(row) => row.retention_pct}
            unit="percent"
            rowLabel="Week of"
            columnLabel="Week"
            detail={(row) =>
              `${formatNumber(row.cohort_size)} users in cohort${row.is_complete ? '' : ' · week not finished'}`
            }
          />

          <p className="text-2xs text-muted-foreground">
            At this grain the largest cohort holds fourteen users, so a single cell moves in steps
            of seven percentage points. Use this matrix for the diagonal pattern, not for the
            numbers in it.
          </p>
        </div>
      </ChartCard>

      <ChartCard
        title="Cumulative revenue per cohort"
        definition="Cumulative ARPU by months since signup — total revenue from the cohort divided by everyone in it, including the people who never paid."
        {...revenue.boundary}
        isEmpty={revenueSeries.data.length === 0 && revenue.boundary.isEmpty}
        emptyMessage={floorEmptyMessage}
        actions={floorControl}
        onExport={() =>
          exportRows('cohort-revenue-cumulative', revenue.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <TimeSeriesChart
            data={revenueSeries.data}
            xKey="month_n"
            xFormat="raw"
            unit="usd"
            series={revenueSeries.series}
          />

          <p className="text-2xs text-muted-foreground">
            The cohorts do not start at the same month, and that is the data rather than a
            rendering fault: the May cohort recorded its first revenue in month 1, so its line
            begins there. ARPU here is measured across the whole cohort, which is why a $9.99
            subscription shows up as about $0.20 — two payers out of a hundred users.
          </p>

          <DataTable
            rows={revenue.rows}
            columns={revenueColumns}
            rowKey={(row) => `${row.cohort_month}-${row.month_n}`}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="LTV against acquisition cost"
        definition="What each channel's users have paid so far against what they cost to acquire. Revenue to date, not a projection — a young cohort has had little time to earn back its spend."
        {...ltv.boundary}
        emptyMessage={floorEmptyMessage}
        actions={floorControl}
        onExport={() =>
          exportRows('cohort-ltv-by-channel', ltv.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={ltv.rows}
            categoryKey="channel"
            unit="usd"
            categoryWidth={130}
            height={Math.max(220, ltv.rows.length * 30)}
            series={[
              { key: 'cac_usd', label: 'CAC per user' },
              { key: 'ltv_per_acquired_usd', label: 'Revenue per acquired user' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            Net contribution is negative for every paid channel here, and that is what the window
            says rather than a verdict on the channels: these users signed up within the last
            three months, subscriptions earn $9.99 a month, and acquisition was paid up front. The
            LTV-to-CAC ratio is blank for organic channels because their CAC is zero — a ratio
            with an empty denominator is undefined, not zero.
          </p>

          <DataTable
            rows={ltv.rows}
            columns={ltvColumns}
            rowKey={(row) => row.channel}
            maxHeight="22rem"
          />
        </div>
      </ChartCard>
    </div>
  )
}
