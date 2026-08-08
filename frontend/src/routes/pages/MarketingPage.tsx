import { useMemo, useState } from 'react'

import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { ScatterQuadrant, type QuadrantPoint } from '@/components/charts/ScatterQuadrant'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { exportRows } from '@/lib/csv'
import {
  EMPTY,
  formatCurrency,
  formatNumber,
  formatPercent,
  formatRatioAsPercent,
  isAbsent,
} from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * Acquisition: what each channel costs, what it returns, and whether it has paid back.
 *
 * `avg_completion_rate` is a fraction, not a pre-multiplied percentage
 * -----------------------------------------------------------------
 * Every other percentage in this API arrives already multiplied — `conversion_pct: 7.14` means
 * 7.14%. This column does not: it comes back as `0.536`, meaning 53.6%. Two of the three
 * endpoints on this page carry it. Passing it to `formatPercent` renders "0.5%", which is not a
 * rounding error but a figure wrong by two orders of magnitude, and it looks entirely plausible
 * next to a real completion rate. It goes through {@link formatRatioAsPercent} instead, which
 * exists as a separate function rather than a flag precisely so the wrong call cannot look like
 * the right one.
 *
 * `median_ltv_usd` and `median_cac_usd` are population figures repeated on every row
 * -------------------------------------------------------------------------------
 * Both come back identical across all twelve channels — 0.00 and 14.10 on the live window. They
 * are not that channel's medians; they are the population medians that define where the quadrant
 * boundaries fall. Rendered as table columns they would read as per-channel measurements that
 * happen to be suspiciously equal, so they are stated once, as the thresholds they are.
 *
 * A null `payback_months` means two different things, and `payback_band` says which
 * ------------------------------------------------------------------------------
 * It is null for all twelve channels here, for two unrelated reasons: an organic channel has no
 * acquisition cost to recover ("no acquisition cost"), and a paid channel has not earned its
 * spend back yet ("not yet recovered"). Collapsing both to a dash would merge "nothing to pay
 * back" with "hasn't paid back", which are opposite readings. The band is shown beside it.
 *
 * The quadrant is the API's, and `is_profitable` does not follow from `is_paid`
 * -------------------------------------------------------------------------
 * Email is `is_paid: false` yet carries a CAC of $0.75 and is not profitable; the four true
 * zero-cost channels are. Nothing here recomputes either field — a client-derived boundary would
 * eventually disagree with the API about which channels are worth funding, invisibly.
 *
 * All three panels are empty at the API's default floor
 * --------------------------------------------------
 * `min_cohort_size` defaults to 30 and the largest channel acquired 28 users, so every panel
 * returns nothing until the floor is lowered. As on the cohorts page, it is a control initialised
 * to the API's own default rather than quietly reduced.
 */

/** Floors offered by the control. 30 is the API's default and stays first. */
const COHORT_SIZE_FLOORS = [30, 20, 10, 5, 1] as const

export function MarketingPage() {
  const { window } = useFilters()
  const [minCohortSize, setMinCohortSize] = useState<number>(COHORT_SIZE_FLOORS[0])

  const extra = { min_cohort_size: minCohortSize }

  const attribution = usePanel('/marketing/channel-attribution', { extra })
  const ltvToCac = usePanel('/marketing/ltv-to-cac', { extra })
  const payback = usePanel('/marketing/cac-payback', { extra })

  const floorControl = (
    <Select
      value={String(minCohortSize)}
      onValueChange={(value) => setMinCohortSize(Number(value))}
    >
      <SelectTrigger className="h-7 w-40 text-xs" aria-label="Minimum users per channel">
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
    `No channel in this window acquired ${minCohortSize} users, so every one of them was filtered ` +
    `out. Lower the minimum to include smaller channels — with a handful of users each, one ` +
    `conversion moves the rate by tens of points, which is the reason for the floor.`

  const points = useMemo<QuadrantPoint[]>(
    () =>
      ltvToCac.rows.map((row) => ({
        label: row.channel,
        // x is what a user costs, y is what they return. Both taken as given.
        x: row.cac_usd,
        y: row.ltv_per_user_usd,
        size: row.users_acquired,
        quadrant: row.quadrant,
        isProfitable: row.is_profitable,
        ratio: row.ltv_to_cac_ratio,
      })),
    [ltvToCac.rows],
  )

  /** The population medians that place the quadrant boundaries. Identical on every row. */
  const thresholds = ltvToCac.rows[0]

  const attributionColumns: Column<(typeof attribution.rows)[number]>[] = [
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
    {
      ...numericColumn('share_of_users_pct', 'Share of users', (row) => row.share_of_users_pct, 'percent'),
      render: (row) => formatPercent(row.share_of_users_pct),
    },
    {
      ...numericColumn(
        'share_of_revenue_pct',
        'Share of revenue',
        (row) => row.share_of_revenue_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.share_of_revenue_pct),
    },
    numericColumn('never_activated', 'Never activated', (row) => row.never_activated, 'users'),
    {
      ...numericColumn(
        'never_activated_pct',
        'Never activated',
        (row) => row.never_activated_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.never_activated_pct),
    },
    numericColumn('avg_sessions', 'Mean sessions', (row) => row.avg_sessions, 'sessions'),
    numericColumn('avg_watch_hours', 'Mean watch hours', (row) => row.avg_watch_hours, 'hours'),
    numericColumn('avg_titles_watched', 'Mean titles', (row) => row.avg_titles_watched),
    {
      // A 0-1 fraction. See the module docstring — `formatPercent` would render 0.62 as "0.6%".
      ...numericColumn(
        'avg_completion_rate',
        'Mean completion',
        (row) => row.avg_completion_rate,
      ),
      render: (row) => formatRatioAsPercent(row.avg_completion_rate),
    },
    numericColumn('converted_users', 'Converted', (row) => row.converted_users, 'users'),
    {
      ...numericColumn('conversion_pct', 'Conversion', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    numericColumn('churned_users', 'Churned', (row) => row.churned_users, 'users'),
    {
      ...numericColumn('churn_pct', 'Churn', (row) => row.churn_pct, 'percent'),
      render: (row) => formatPercent(row.churn_pct),
    },
    {
      ...numericColumn('cac_usd', 'CAC per user', (row) => row.cac_usd, 'usd'),
      render: (row) => formatCurrency(row.cac_usd),
    },
    {
      ...numericColumn('total_spend_usd', 'Spend', (row) => row.total_spend_usd, 'usd'),
      render: (row) => formatCurrency(row.total_spend_usd),
    },
    {
      ...numericColumn('total_revenue_usd', 'Revenue', (row) => row.total_revenue_usd, 'usd'),
      render: (row) => formatCurrency(row.total_revenue_usd),
    },
    {
      ...numericColumn('current_mrr_usd', 'Current MRR', (row) => row.current_mrr_usd, 'usd'),
      render: (row) => formatCurrency(row.current_mrr_usd),
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

  const ltvColumns: Column<(typeof ltvToCac.rows)[number]>[] = [
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
    {
      key: 'quadrant',
      header: 'Quadrant',
      value: (row) => row.quadrant,
      render: (row) => <span className="capitalize">{row.quadrant}</span>,
    },
    {
      key: 'is_profitable',
      header: 'Profitable',
      value: (row) => row.is_profitable,
      render: (row) =>
        row.is_profitable ? (
          <span className="text-muted-foreground">yes</span>
        ) : (
          <Badge variant="warning">not yet</Badge>
        ),
    },
    numericColumn('users_acquired', 'Acquired', (row) => row.users_acquired, 'users'),
    numericColumn('converted', 'Converted', (row) => row.converted, 'users'),
    {
      ...numericColumn('conversion_pct', 'Conversion', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    {
      ...numericColumn('cac_usd', 'CAC per user', (row) => row.cac_usd, 'usd'),
      render: (row) => formatCurrency(row.cac_usd),
    },
    {
      ...numericColumn('ltv_per_user_usd', 'LTV per user', (row) => row.ltv_per_user_usd, 'usd'),
      render: (row) => formatCurrency(row.ltv_per_user_usd),
    },
    {
      // Null where CAC is zero: a ratio with an empty denominator is undefined, not infinite.
      ...numericColumn('ltv_to_cac_ratio', 'LTV to CAC', (row) => row.ltv_to_cac_ratio),
      render: (row) =>
        isAbsent(row.ltv_to_cac_ratio) ? EMPTY : `${formatNumber(row.ltv_to_cac_ratio, 2)}×`,
    },
    numericColumn('avg_watch_hours', 'Mean watch hours', (row) => row.avg_watch_hours, 'hours'),
    {
      // A 0-1 fraction, as on the attribution table.
      ...numericColumn('avg_completion_rate', 'Mean completion', (row) => row.avg_completion_rate),
      render: (row) => formatRatioAsPercent(row.avg_completion_rate),
    },
    {
      ...numericColumn('total_spend_usd', 'Spend', (row) => row.total_spend_usd, 'usd'),
      render: (row) => formatCurrency(row.total_spend_usd),
    },
    {
      ...numericColumn('total_revenue_usd', 'Revenue', (row) => row.total_revenue_usd, 'usd'),
      render: (row) => formatCurrency(row.total_revenue_usd),
    },
  ]

  const paybackColumns: Column<(typeof payback.rows)[number]>[] = [
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
    {
      ...numericColumn('cac_per_user_usd', 'CAC per user', (row) => row.cac_per_user_usd, 'usd'),
      render: (row) => formatCurrency(row.cac_per_user_usd),
    },
    {
      ...numericColumn('total_spend_usd', 'Spend', (row) => row.total_spend_usd, 'usd'),
      render: (row) => formatCurrency(row.total_spend_usd),
    },
    {
      ...numericColumn(
        'revenue_to_date_usd',
        'Revenue to date',
        (row) => row.revenue_to_date_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.revenue_to_date_usd),
    },
    {
      ...numericColumn(
        'revenue_per_user_usd',
        'Revenue per user',
        (row) => row.revenue_per_user_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.revenue_per_user_usd),
    },
    {
      ...numericColumn('net_position_usd', 'Net position', (row) => row.net_position_usd, 'usd'),
      render: (row) => (
        <span className={row.net_position_usd < 0 ? 'text-destructive' : undefined}>
          {formatCurrency(row.net_position_usd)}
        </span>
      ),
    },
    {
      // Absent for two opposite reasons; the band beside it distinguishes them.
      ...numericColumn('payback_months', 'Months to payback', (row) => row.payback_months),
      render: (row) => (isAbsent(row.payback_months) ? EMPTY : formatNumber(row.payback_months)),
    },
    {
      key: 'payback_band',
      header: 'Status',
      value: (row) => row.payback_band,
      render: (row) => (
        <span className="capitalize">
          {row.payback_band === 'not yet recovered' ? (
            <Badge variant="warning">not yet recovered</Badge>
          ) : (
            <span className="text-muted-foreground">{row.payback_band}</span>
          )}
        </span>
      ),
    },
    {
      ...numericColumn('ltv_to_cac_ratio', 'LTV to CAC', (row) => row.ltv_to_cac_ratio),
      render: (row) =>
        isAbsent(row.ltv_to_cac_ratio) ? EMPTY : `${formatNumber(row.ltv_to_cac_ratio, 2)}×`,
    },
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="Channel attribution"
        definition="Every acquisition channel by what it brought in and what those users did. Share of users against share of revenue is the comparison worth making — the two rarely match."
        {...attribution.boundary}
        emptyMessage={floorEmptyMessage}
        actions={floorControl}
        onExport={() =>
          exportRows('marketing-channel-attribution', attribution.rows, {
            window: window ?? undefined,
          })
        }
      >
        <div className="space-y-4">
          {/* Grouped rather than stacked. Both series are shares of their own whole and each
              sums to 100% across channels, so a stack would draw a meaningless 200% total. */}
          <CategoryBarChart
            data={attribution.rows}
            categoryKey="channel"
            unit="percent"
            categoryWidth={140}
            height={Math.max(240, attribution.rows.length * 30)}
            series={[
              { key: 'share_of_users_pct', label: 'Share of users' },
              { key: 'share_of_revenue_pct', label: 'Share of revenue' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            A channel whose revenue share exceeds its user share is punching above its weight. On
            this window all the revenue sits with organic channels, which is a statement about
            three paying users rather than a durable finding — the paid channels have converted
            nobody yet.
          </p>

          <DataTable
            rows={attribution.rows}
            columns={attributionColumns}
            rowKey={(row) => row.channel}
            maxHeight="24rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="LTV against CAC"
        definition="What a channel's user returns against what they cost. The diagonal is break-even: above it a channel pays for itself, below it does not. Bubble area is users acquired."
        {...ltvToCac.boundary}
        isEmpty={points.length === 0 && ltvToCac.boundary.isEmpty}
        emptyMessage={floorEmptyMessage}
        actions={floorControl}
        onExport={() =>
          exportRows('marketing-ltv-to-cac', ltvToCac.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <ScatterQuadrant
            points={points}
            xLabel="CAC per user (USD)"
            yLabel="LTV per user (USD)"
            unit="usd"
            sizeLabel="users acquired"
          />

          {thresholds && (
            <p className="text-2xs text-muted-foreground">
              The quadrant boundaries are the population medians —{' '}
              {formatCurrency(thresholds.median_cac_usd)} CAC and{' '}
              {formatCurrency(thresholds.median_ltv_usd)} LTV — not each channel&apos;s own
              medians, which is why the API repeats them on every row. The quadrant label and the
              profitability verdict are the server&apos;s and are not recomputed here.
            </p>
          )}

          <p className="text-2xs text-muted-foreground">
            Organic channels sit on the left edge at zero cost, and their ratio is blank rather
            than large: dividing by a CAC of nothing yields no measurement. Note that a channel
            can be unpaid and still carry a cost — Email runs at $0.75 a user — so the profitable
            set is not simply the unpaid one.
          </p>

          <DataTable
            rows={ltvToCac.rows}
            columns={ltvColumns}
            rowKey={(row) => row.channel}
            maxHeight="24rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="CAC payback"
        definition="How long each channel needs to earn back what it spent, against what it has earned so far. Revenue to date, not a projection."
        {...payback.boundary}
        emptyMessage={floorEmptyMessage}
        actions={floorControl}
        onExport={() =>
          exportRows('marketing-cac-payback', payback.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={payback.rows}
            categoryKey="channel"
            unit="usd"
            hideLegend
            categoryWidth={140}
            height={Math.max(220, payback.rows.length * 28)}
            series={[{ key: 'net_position_usd', label: 'Net position' }]}
          />

          <p className="text-2xs text-muted-foreground">
            No channel shows a payback figure on this window, for two opposite reasons the status
            column separates: an organic channel has no acquisition cost to recover, while a paid
            channel has not recovered its spend yet. Reading both as a blank would merge
            &ldquo;nothing to pay back&rdquo; with &ldquo;hasn&apos;t paid back&rdquo;. Three
            months of a $9.99 subscription against an up-front cost of $14 to $32 per user is why
            the paid rows sit where they do.
          </p>

          <DataTable
            rows={payback.rows}
            columns={paybackColumns}
            rowKey={(row) => row.channel}
            maxHeight="24rem"
          />
        </div>
      </ChartCard>
    </div>
  )
}
