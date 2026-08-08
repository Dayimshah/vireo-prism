import { useMemo, useState } from 'react'

import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { TimeSeriesChart } from '@/components/charts/TimeSeriesChart'
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
 * Revenue: what it is, where it moved, and who pays.
 *
 * The one place in this app where a null is treated as a zero
 * ---------------------------------------------------------
 * Everywhere else, an absent figure is rendered as a dash and never as zero — that discipline is
 * the reason this dashboard can be trusted. `/monetization/mrr-movement` is the documented
 * exception, and it is worth being precise about why.
 *
 * The endpoint returns `reactivation_mrr`, `expansion_mrr`, `contraction_mrr` and `churn_mrr` as
 * `null` in a month where that kind of movement did not occur. It also returns `opening_mrr`,
 * `closing_mrr` and `net_change_mrr` as computed figures. Those two facts are only consistent if
 * an absent movement contributes nothing: on the live window, May is 0 + 301.90 = 301.90, June is
 * 301.90 + 32.46 − 9.99 = 324.37, and July is 324.37 + 66.44 = 390.81. Each balances exactly,
 * with the nulls read as no movement.
 *
 * So in a *waterfall* the null is zero, because the identity the waterfall draws demands it — a
 * dash cannot be added to a running total. In the *table* it stays a dash, because "no
 * reactivations happened" and "reactivations were measured at zero" are different statements and
 * the table is where a reader goes for the precise reading. The two views are consistent: the
 * waterfall is an accounting identity, the table is a record of observations.
 *
 * `churn_mrr` arrives already negative
 * ----------------------------------
 * It comes back as `-9.99`, not `9.99`. Negating it here to make it "a loss" would add it back to
 * the total and break the identity above. The sign is the API's and is passed through untouched.
 *
 * ARPU and ARPPU have different denominators, and the gap is the business
 * --------------------------------------------------------------------
 * `arpu_usd` divides revenue by *active* users; `arppu_usd` divides it by *paying* users. On the
 * live window that is $1.43 against $8.68 — a factor of six, which is just another way of saying
 * 16% of active users pay. Plotting them on one axis without saying so invites a reader to treat
 * the lower line as a decline.
 *
 * Trial conversion is empty at the API's default floor
 * --------------------------------------------------
 * `min_cohort_size` defaults to 30 and the largest trial plan here has 7 trials, so the panel
 * returns nothing until the floor is lowered. As on the cohorts page, the floor is a control
 * initialised to the API's own default rather than quietly reduced, and the empty state names it.
 */

/** Floors offered by the trial-conversion control. 30 is the API's default and stays first. */
const COHORT_SIZE_FLOORS = [30, 10, 5, 1] as const

/** The four movement components, in the order a waterfall reads them. */
const MOVEMENT_SERIES = [
  { key: 'new_mrr', label: 'New' },
  { key: 'reactivation_mrr', label: 'Reactivation' },
  { key: 'expansion_mrr', label: 'Expansion' },
  { key: 'contraction_mrr', label: 'Contraction' },
  { key: 'churn_mrr', label: 'Churn' },
] as const

/** One month of movement, with absent components resolved to zero for the chart only. */
interface MovementDatum extends Record<string, CsvValue> {
  month: string
  month_label: string
  new_mrr: number
  reactivation_mrr: number
  expansion_mrr: number
  contraction_mrr: number
  churn_mrr: number
  opening_mrr: number
  closing_mrr: number
}

export function MonetizationPage() {
  const { window } = useFilters()
  const [minCohortSize, setMinCohortSize] = useState<number>(COHORT_SIZE_FLOORS[0])

  const arpu = usePanel('/monetization/arpu-trend')
  const movement = usePanel('/monetization/mrr-movement')
  const trials = usePanel('/monetization/trial-conversion', {
    extra: { min_cohort_size: minCohortSize },
  })
  const deciles = usePanel('/monetization/conversion-by-watch-decile')

  // Absent components resolved to zero, for the chart only. See the module docstring: the
  // identity opening + movements = closing is the API's, and a dash cannot be added to it.
  const movementChart = useMemo<MovementDatum[]>(
    () =>
      movement.rows.map((row) => ({
        month: row.month,
        month_label: formatMonthLabel(row.month),
        new_mrr: row.new_mrr,
        reactivation_mrr: row.reactivation_mrr ?? 0,
        expansion_mrr: row.expansion_mrr ?? 0,
        contraction_mrr: row.contraction_mrr ?? 0,
        // Already negative from the API. Not negated again.
        churn_mrr: row.churn_mrr ?? 0,
        opening_mrr: row.opening_mrr,
        closing_mrr: row.closing_mrr,
      })),
    [movement.rows],
  )

  const arpuColumns: Column<(typeof arpu.rows)[number]>[] = [
    {
      key: 'month',
      header: 'Month',
      value: (row) => row.month,
      render: (row) => formatMonthLabel(row.month),
      className: 'font-medium',
    },
    numericColumn('active_users', 'Active users', (row) => row.active_users, 'users'),
    numericColumn('paying_users', 'Paying users', (row) => row.paying_users, 'users'),
    {
      ...numericColumn('paying_share_pct', 'Paying share', (row) => row.paying_share_pct, 'percent'),
      render: (row) => formatPercent(row.paying_share_pct),
    },
    {
      ...numericColumn('mrr_usd', 'MRR', (row) => row.mrr_usd, 'usd'),
      render: (row) => formatCurrency(row.mrr_usd),
    },
    {
      ...numericColumn('arpu_usd', 'ARPU (all active)', (row) => row.arpu_usd, 'usd'),
      render: (row) => formatCurrency(row.arpu_usd),
    },
    {
      ...numericColumn('arppu_usd', 'ARPPU (payers only)', (row) => row.arppu_usd, 'usd'),
      render: (row) => formatCurrency(row.arppu_usd),
    },
    {
      ...numericColumn('avg_list_price_usd', 'Mean list price', (row) => row.avg_list_price_usd, 'usd'),
      render: (row) => formatCurrency(row.avg_list_price_usd),
    },
    {
      // Realised revenue against list price — the discounting gap, around 98% here.
      ...numericColumn(
        'realised_vs_list_pct',
        'Realised vs list',
        (row) => row.realised_vs_list_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.realised_vs_list_pct),
    },
    {
      // Null in the first month: there is no previous month to change from, which is not a
      // change of zero.
      ...numericColumn('arpu_change_usd', 'ARPU change', (row) => row.arpu_change_usd, 'usd'),
      render: (row) => (isAbsent(row.arpu_change_usd) ? EMPTY : formatCurrency(row.arpu_change_usd)),
    },
  ]

  const movementColumns: Column<(typeof movement.rows)[number]>[] = [
    {
      key: 'month',
      header: 'Month',
      value: (row) => row.month,
      render: (row) => formatMonthLabel(row.month),
      className: 'font-medium',
    },
    {
      ...numericColumn('opening_mrr', 'Opening', (row) => row.opening_mrr, 'usd'),
      render: (row) => formatCurrency(row.opening_mrr),
    },
    {
      ...numericColumn('new_mrr', 'New', (row) => row.new_mrr, 'usd'),
      render: (row) => formatCurrency(row.new_mrr),
    },
    {
      // A dash here, a zero in the chart. The two views answer different questions — see the
      // module docstring.
      ...numericColumn('reactivation_mrr', 'Reactivation', (row) => row.reactivation_mrr, 'usd'),
      render: (row) => (isAbsent(row.reactivation_mrr) ? EMPTY : formatCurrency(row.reactivation_mrr)),
    },
    {
      ...numericColumn('expansion_mrr', 'Expansion', (row) => row.expansion_mrr, 'usd'),
      render: (row) => (isAbsent(row.expansion_mrr) ? EMPTY : formatCurrency(row.expansion_mrr)),
    },
    {
      ...numericColumn('contraction_mrr', 'Contraction', (row) => row.contraction_mrr, 'usd'),
      render: (row) => (isAbsent(row.contraction_mrr) ? EMPTY : formatCurrency(row.contraction_mrr)),
    },
    {
      ...numericColumn('churn_mrr', 'Churn', (row) => row.churn_mrr, 'usd'),
      render: (row) =>
        isAbsent(row.churn_mrr) ? (
          EMPTY
        ) : (
          <span className="text-destructive">{formatCurrency(row.churn_mrr)}</span>
        ),
    },
    {
      ...numericColumn('closing_mrr', 'Closing', (row) => row.closing_mrr, 'usd'),
      render: (row) => formatCurrency(row.closing_mrr),
    },
    {
      ...numericColumn('net_change_mrr', 'Net change', (row) => row.net_change_mrr, 'usd'),
      render: (row) => (
        <span className={row.net_change_mrr < 0 ? 'text-destructive' : undefined}>
          {formatCurrency(row.net_change_mrr)}
        </span>
      ),
    },
    numericColumn('new_subscribers', 'New subs', (row) => row.new_subscribers, 'users'),
    numericColumn('churned_subscribers', 'Churned subs', (row) => row.churned_subscribers, 'users'),
    numericColumn(
      'reactivated_subscribers',
      'Reactivated subs',
      (row) => row.reactivated_subscribers,
      'users',
    ),
    {
      // Undefined in a month with no opening MRR to retain — the first month, and any month
      // whose opening balance was zero.
      ...numericColumn(
        'net_revenue_retention_pct',
        'Net revenue retention',
        (row) => row.net_revenue_retention_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.net_revenue_retention_pct),
    },
  ]

  const trialColumns: Column<(typeof trials.rows)[number]>[] = [
    {
      key: 'trial_plan',
      header: 'Plan',
      value: (row) => row.trial_plan,
      className: 'font-medium',
    },
    { key: 'plan_tier', header: 'Tier', value: (row) => row.plan_tier },
    {
      ...numericColumn('list_price_usd', 'List price', (row) => row.list_price_usd, 'usd'),
      render: (row) => formatCurrency(row.list_price_usd),
    },
    numericColumn('trials_started', 'Trials started', (row) => row.trials_started),
    numericColumn('trials_converted', 'Converted', (row) => row.trials_converted),
    {
      ...numericColumn('conversion_pct', 'Conversion', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    {
      ...numericColumn('avg_days_to_convert', 'Mean days to convert', (row) => row.avg_days_to_convert),
      render: (row) => (isAbsent(row.avg_days_to_convert) ? EMPTY : formatNumber(row.avg_days_to_convert, 1)),
    },
    {
      ...numericColumn(
        'median_days_to_convert',
        'Median days',
        (row) => row.median_days_to_convert,
      ),
      render: (row) =>
        isAbsent(row.median_days_to_convert) ? EMPTY : formatNumber(row.median_days_to_convert, 1),
    },
    numericColumn('switched_plan', 'Switched plan', (row) => row.switched_plan),
    numericColumn('chose_annual', 'Chose annual', (row) => row.chose_annual),
    {
      ...numericColumn(
        'avg_converted_mrr_usd',
        'Mean converted MRR',
        (row) => row.avg_converted_mrr_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.avg_converted_mrr_usd),
    },
    {
      ...numericColumn(
        'total_converted_mrr_usd',
        'Total converted MRR',
        (row) => row.total_converted_mrr_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.total_converted_mrr_usd),
    },
    numericColumn('still_paying', 'Still paying', (row) => row.still_paying, 'users'),
    {
      ...numericColumn(
        'post_conversion_retention_pct',
        'Retained after converting',
        (row) => row.post_conversion_retention_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.post_conversion_retention_pct),
    },
  ]

  const decileColumns: Column<(typeof deciles.rows)[number]>[] = [
    { key: 'watch_decile', header: 'Decile', value: (row) => row.watch_decile, align: 'right' },
    numericColumn('users', 'Users', (row) => row.users, 'users'),
    {
      key: 'range',
      header: 'Watch hours in band',
      value: (row) => `${row.min_watch_hours}–${row.max_watch_hours}`,
      align: 'right',
      render: (row) => `${formatNumber(row.min_watch_hours, 1)}–${formatNumber(row.max_watch_hours, 1)}`,
    },
    numericColumn('avg_watch_hours', 'Mean watch hours', (row) => row.avg_watch_hours, 'hours'),
    numericColumn('avg_completions', 'Mean completions', (row) => row.avg_completions),
    numericColumn('avg_sessions', 'Mean sessions', (row) => row.avg_sessions, 'sessions'),
    numericColumn('started_trial', 'Started trial', (row) => row.started_trial),
    numericColumn('converted_paid', 'Converted', (row) => row.converted_paid),
    {
      ...numericColumn('trial_rate_pct', 'Trial rate', (row) => row.trial_rate_pct, 'percent'),
      render: (row) => formatPercent(row.trial_rate_pct),
    },
    {
      ...numericColumn('conversion_pct', 'Conversion', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    {
      ...numericColumn('conversion_lift', 'Lift vs average', (row) => row.conversion_lift),
      render: (row) => (isAbsent(row.conversion_lift) ? EMPTY : `${formatNumber(row.conversion_lift, 2)}×`),
    },
    {
      // Undefined for a decile with no payers: there is nobody to have retained.
      ...numericColumn(
        'paid_retention_pct',
        'Still paying',
        (row) => row.paid_retention_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.paid_retention_pct),
    },
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="ARPU and ARPPU"
        definition="Revenue per active user against revenue per paying user. The gap between the two lines is the share of the audience that pays — not a decline in either."
        {...arpu.boundary}
        onExport={() =>
          exportRows('monetization-arpu-trend', arpu.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <TimeSeriesChart
            data={arpu.rows}
            xKey="month"
            xFormat="month"
            unit="usd"
            series={[
              { key: 'arpu_usd', label: 'ARPU (all active users)' },
              { key: 'arppu_usd', label: 'ARPPU (paying users only)' },
              { key: 'avg_list_price_usd', label: 'Mean list price' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            The two revenue lines share a scale but not a denominator: ARPU divides by every active
            user, ARPPU divides by the paying ones. The mean list price sits just above ARPPU, and
            the gap between those two is discounting — realised revenue runs at roughly 98% of
            list.
          </p>

          <DataTable
            rows={arpu.rows}
            columns={arpuColumns}
            rowKey={(row) => row.month}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="MRR movement"
        definition="How MRR got from its opening to its closing balance each month. New, reactivation and expansion add; contraction and churn subtract, and arrive already signed negative."
        {...movement.boundary}
        isEmpty={movementChart.length === 0 && movement.boundary.isEmpty}
        onExport={() =>
          exportRows('monetization-mrr-movement', movement.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          {/* Stacked, and legitimately: these components sum to the net change the API reports,
              which is the one condition under which stacking is not misleading. Churn is
              negative, so it stacks downward from the axis. */}
          <CategoryBarChart
            data={movementChart}
            categoryKey="month_label"
            layout="vertical"
            stacked
            unit="usd"
            height={240}
            series={MOVEMENT_SERIES.map((spec) => ({ ...spec }))}
          />

          <TimeSeriesChart
            data={movement.rows}
            xKey="month"
            xFormat="month"
            unit="usd"
            series={[
              { key: 'opening_mrr', label: 'Opening MRR' },
              { key: 'closing_mrr', label: 'Closing MRR' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            A movement that did not happen comes back undefined rather than zero, and the two views
            here treat that differently on purpose. The chart reads it as no contribution, because
            the opening balance plus these components has to equal the closing balance the API
            reports. The table below leaves it as a dash, because &ldquo;no reactivations
            occurred&rdquo; is a different statement from &ldquo;reactivations totalled
            zero&rdquo;.
          </p>

          <DataTable
            rows={movement.rows}
            columns={movementColumns}
            rowKey={(row) => row.month}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Does watching more lead to paying?"
        definition="Users split into ten equal bands by watch hours, with the conversion rate of each. Lift compares a band's conversion with the average across all of them."
        {...deciles.boundary}
        onExport={() =>
          exportRows('monetization-conversion-by-watch-decile', deciles.rows, {
            window: window ?? undefined,
          })
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={deciles.rows}
            categoryKey="watch_decile"
            layout="vertical"
            unit="percent"
            hideLegend
            height={220}
            formatCategory={(value) => `D${value}`}
            series={[{ key: 'conversion_pct', label: 'Conversion' }]}
          />

          <p className="text-2xs text-muted-foreground">
            Eleven users per band and two conversions in total across all ten, so this panel shows
            a direction rather than a relationship: both conversions sit in the upper half, which
            is consistent with heavy viewers being likelier to pay and is nowhere near enough
            evidence to size the effect. Note too that the trial rate is 0% everywhere while
            conversions are not — these users subscribed without taking a trial first.
          </p>

          <DataTable
            rows={deciles.rows}
            columns={decileColumns}
            rowKey={(row) => String(row.watch_decile)}
            maxHeight="24rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Trial conversion by plan"
        definition="Trials started on each plan and how many became paying subscriptions, with how long that took."
        {...trials.boundary}
        emptyMessage={
          `No trial plan in this window reached ${minCohortSize} trials, so all of them were filtered out. ` +
          `Lower the minimum to include smaller plans — with a handful of trials each, a single conversion ` +
          `swings the rate by tens of points, which is the reason for the floor.`
        }
        actions={
          <Select
            value={String(minCohortSize)}
            onValueChange={(value) => setMinCohortSize(Number(value))}
          >
            <SelectTrigger className="h-7 w-40 text-xs" aria-label="Minimum trials per plan">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COHORT_SIZE_FLOORS.map((size) => (
                <SelectItem key={size} value={String(size)} className="text-xs">
                  {size === 30 ? '30+ trials (default)' : `${size}+ trials`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        onExport={() =>
          exportRows('monetization-trial-conversion', trials.rows, { window: window ?? undefined })
        }
      >
        <DataTable
          rows={trials.rows}
          columns={trialColumns}
          rowKey={(row) => row.trial_plan}
        />
      </ChartCard>
    </div>
  )
}
