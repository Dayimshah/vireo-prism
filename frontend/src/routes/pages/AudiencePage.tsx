import { useState } from 'react'

import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { exportRows } from '@/lib/csv'
import { formatDate, formatMonthLabel } from '@/lib/dates'
import {
  EMPTY,
  formatCurrency,
  formatNumber,
  formatPercent,
  formatRatioAsPercent,
  humanize,
  isAbsent,
} from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * Who the audience is: where they are, what they watch on, and who is about to leave.
 *
 * Three of these five endpoints do not take the full date window, and that is deliberate rather
 * than an oversight. `/geo/country-ranking` takes `date_to` alone — it is an as-of snapshot, not
 * a period measurement. `/users/rfm-segments` and `/churn/risk-scorecard` take no window at all,
 * because both describe the present state of every user on record. `windowParamsFor` knows which
 * is which, so nothing here has to; a window param sent to a route that does not declare it is a
 * 422, not a silently ignored extra.
 *
 * The device breakdown is two different populations in one response
 * --------------------------------------------------------------
 * `/geo/device-breakdown` unions two queries under a `row_type` discriminator, and they do not
 * merely differ in framing — they count different things and populate different columns.
 *
 * * **`signup`** rows count each user once, by the device they signed up on. They carry
 *   `revenue_usd`, `paying_users`, `conversion_pct` and `avg_completion_rate`, and return
 *   `avg_session_minutes` as `null`.
 * * **`usage`** rows count each user once *per device they used*. They carry
 *   `avg_session_minutes`, and return revenue, payers, conversion and completion as `null`.
 *
 * The second point matters most: the seven usage rows total roughly 1,497 users against a base of
 * about 600, because a person who watches on a phone and a TV appears in both. Both sets sum to
 * 100% within themselves, so a combined table would show shares adding to 200% over a user count
 * that is not a user count. They are rendered as two tables with two column sets, and the usage
 * one says out loud that its users overlap.
 *
 * Completion rates here are 0-1 fractions
 * -------------------------------------
 * `avg_completion_rate` on both geo endpoints, and `completion_rate` on the risk scorecard,
 * arrive as `0.582` meaning 58.2% — unlike every `_pct` column in this API, which is
 * pre-multiplied. {@link formatRatioAsPercent} handles them; {@link formatPercent} would render
 * "0.6%", wrong by two orders of magnitude and entirely plausible on screen.
 *
 * The risk score is a sum of its five components
 * --------------------------------------------
 * `recency_points + frequency_points + engagement_points + volume_points + tenure_points`
 * equals `risk_score` exactly — 24 + 20 + 11 + 12 + 5 = 72 on the top row of the live response.
 * The components are shown because a score of 72 driven by dormancy needs a different response
 * from a 72 driven by abandoning content, and `primary_driver` names the largest contributor.
 */

/** Floors offered by the country-ranking control. 30 is the API's default and stays first. */
const COUNTRY_SIZE_FLOORS = [30, 20, 10, 5, 1] as const

/** Risk floors offered by the scorecard control. 30 is the API's default. */
const RISK_SCORE_FLOORS = [30, 20, 50, 70] as const

export function AudiencePage() {
  const { window } = useFilters()
  const [minCountrySize, setMinCountrySize] = useState<number>(COUNTRY_SIZE_FLOORS[0])
  const [minRiskScore, setMinRiskScore] = useState<number>(RISK_SCORE_FLOORS[0])

  const countries = usePanel('/geo/country-ranking', {
    extra: { min_cohort_size: minCountrySize },
  })
  const devices = usePanel('/geo/device-breakdown')
  const rfm = usePanel('/users/rfm-segments')
  const churnReasons = usePanel('/churn/reason-mix')
  const risk = usePanel('/churn/risk-scorecard', { extra: { min_risk_score: minRiskScore } })

  const signupDevices = devices.rows.filter((row) => row.row_type === 'signup')
  const usageDevices = devices.rows.filter((row) => row.row_type === 'usage')

  const countryColumns: Column<(typeof countries.rows)[number]>[] = [
    {
      key: 'country',
      header: 'Country',
      value: (row) => row.country,
      className: 'font-medium',
    },
    { key: 'region', header: 'Region', value: (row) => row.region },
    {
      key: 'tier_label',
      header: 'Tier',
      value: (row) => row.tier_label,
      render: (row) => (
        <span className="capitalize text-muted-foreground">{row.tier_label}</span>
      ),
    },
    numericColumn('users', 'Users', (row) => row.users, 'users'),
    numericColumn('active_users', 'Active', (row) => row.active_users, 'users'),
    numericColumn('paying_users', 'Paying', (row) => row.paying_users, 'users'),
    {
      ...numericColumn('conversion_pct', 'Conversion', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    {
      ...numericColumn('churn_pct', 'Churn', (row) => row.churn_pct, 'percent'),
      render: (row) => formatPercent(row.churn_pct),
    },
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    numericColumn(
      'watch_hours_per_user',
      'Hours per user',
      (row) => row.watch_hours_per_user,
      'hours',
    ),
    {
      // A 0-1 fraction, not a pre-multiplied percentage. See the module docstring.
      ...numericColumn('avg_completion_rate', 'Mean completion', (row) => row.avg_completion_rate),
      render: (row) => formatRatioAsPercent(row.avg_completion_rate),
    },
    numericColumn('avg_active_days', 'Mean active days', (row) => row.avg_active_days),
    {
      ...numericColumn('revenue_usd', 'Revenue', (row) => row.revenue_usd, 'usd'),
      render: (row) => formatCurrency(row.revenue_usd),
    },
    {
      ...numericColumn('current_mrr_usd', 'Current MRR', (row) => row.current_mrr_usd, 'usd'),
      render: (row) => formatCurrency(row.current_mrr_usd),
    },
    {
      ...numericColumn('arpu_usd', 'ARPU', (row) => row.arpu_usd, 'usd'),
      render: (row) => formatCurrency(row.arpu_usd),
    },
    {
      // Null where a country has no payers: a per-payer figure over no payers is undefined.
      ...numericColumn('arppu_usd', 'ARPPU', (row) => row.arppu_usd, 'usd'),
      render: (row) => formatCurrency(row.arppu_usd),
    },
    {
      ...numericColumn('share_of_users_pct', 'Share of users', (row) => row.share_of_users_pct, 'percent'),
      render: (row) => formatPercent(row.share_of_users_pct),
    },
    {
      ...numericColumn('share_of_watch_pct', 'Share of watch', (row) => row.share_of_watch_pct, 'percent'),
      render: (row) => formatPercent(row.share_of_watch_pct),
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
    {
      // Revenue share over user share. Above 1 means the country earns more than its headcount
      // would suggest; the API computes it, so it is not recomputed here.
      ...numericColumn('revenue_index', 'Revenue index', (row) => row.revenue_index),
      render: (row) =>
        isAbsent(row.revenue_index) ? EMPTY : `${formatNumber(row.revenue_index, 2)}×`,
    },
    numericColumn('watch_rank', 'Watch rank', (row) => row.watch_rank),
    numericColumn('revenue_rank', 'Revenue rank', (row) => row.revenue_rank),
    numericColumn('arpu_rank', 'ARPU rank', (row) => row.arpu_rank),
  ]

  const signupDeviceColumns: Column<(typeof devices.rows)[number]>[] = [
    {
      key: 'form_factor',
      header: 'Form factor',
      value: (row) => row.form_factor,
      className: 'font-medium',
    },
    { key: 'platform', header: 'Platform', value: (row) => row.platform },
    numericColumn('users', 'Users', (row) => row.users, 'users'),
    {
      ...numericColumn('share_of_users_pct', 'Share of users', (row) => row.share_of_users_pct, 'percent'),
      render: (row) => formatPercent(row.share_of_users_pct),
    },
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    {
      ...numericColumn('share_of_watch_pct', 'Share of watch', (row) => row.share_of_watch_pct, 'percent'),
      render: (row) => formatPercent(row.share_of_watch_pct),
    },
    {
      ...numericColumn('avg_completion_rate', 'Mean completion', (row) => row.avg_completion_rate),
      render: (row) => formatRatioAsPercent(row.avg_completion_rate),
    },
    numericColumn('paying_users', 'Paying', (row) => row.paying_users, 'users'),
    {
      ...numericColumn('conversion_pct', 'Conversion', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    {
      ...numericColumn('revenue_usd', 'Revenue', (row) => row.revenue_usd, 'usd'),
      render: (row) => formatCurrency(row.revenue_usd),
    },
  ]

  // A different column set, because the `usage` rows populate different fields. Revenue and
  // conversion are absent by construction there, so listing them would be nine dashes.
  const usageDeviceColumns: Column<(typeof devices.rows)[number]>[] = [
    {
      key: 'form_factor',
      header: 'Form factor',
      value: (row) => row.form_factor,
      className: 'font-medium',
    },
    { key: 'platform', header: 'Platform', value: (row) => row.platform },
    numericColumn('users', 'Users on device', (row) => row.users, 'users'),
    {
      ...numericColumn('share_of_users_pct', 'Share', (row) => row.share_of_users_pct, 'percent'),
      render: (row) => formatPercent(row.share_of_users_pct),
    },
    numericColumn('sessions', 'Sessions', (row) => row.sessions, 'sessions'),
    {
      ...numericColumn(
        'share_of_sessions_pct',
        'Share of sessions',
        (row) => row.share_of_sessions_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.share_of_sessions_pct),
    },
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    {
      ...numericColumn('share_of_watch_pct', 'Share of watch', (row) => row.share_of_watch_pct, 'percent'),
      render: (row) => formatPercent(row.share_of_watch_pct),
    },
    numericColumn(
      'avg_session_minutes',
      'Mean session minutes',
      (row) => row.avg_session_minutes,
    ),
  ]

  const rfmColumns: Column<(typeof rfm.rows)[number]>[] = [
    {
      key: 'rfm_segment',
      header: 'Segment',
      value: (row) => row.rfm_segment,
      className: 'font-medium capitalize',
    },
    numericColumn('users', 'Users', (row) => row.users, 'users'),
    {
      ...numericColumn('pct_of_users', 'Share of users', (row) => row.pct_of_users, 'percent'),
      render: (row) => formatPercent(row.pct_of_users),
    },
    {
      ...numericColumn('pct_of_revenue', 'Share of revenue', (row) => row.pct_of_revenue, 'percent'),
      render: (row) => formatPercent(row.pct_of_revenue),
    },
    numericColumn('avg_recency_decile', 'Recency decile', (row) => row.avg_recency_decile),
    numericColumn('avg_frequency_decile', 'Frequency decile', (row) => row.avg_frequency_decile),
    numericColumn('avg_monetary_decile', 'Monetary decile', (row) => row.avg_monetary_decile),
    numericColumn('avg_days_dormant', 'Mean days dormant', (row) => row.avg_days_dormant),
    numericColumn('avg_sessions', 'Mean sessions', (row) => row.avg_sessions, 'sessions'),
    numericColumn('avg_watch_hours', 'Mean watch hours', (row) => row.avg_watch_hours, 'hours'),
    numericColumn('avg_titles_watched', 'Mean titles', (row) => row.avg_titles_watched),
    numericColumn('avg_genres', 'Mean genres', (row) => row.avg_genres),
    {
      ...numericColumn('total_revenue_usd', 'Revenue', (row) => row.total_revenue_usd, 'usd'),
      render: (row) => formatCurrency(row.total_revenue_usd),
    },
    {
      ...numericColumn('avg_revenue_usd', 'Revenue per user', (row) => row.avg_revenue_usd, 'usd'),
      render: (row) => formatCurrency(row.avg_revenue_usd),
    },
    {
      ...numericColumn('current_mrr_usd', 'Current MRR', (row) => row.current_mrr_usd, 'usd'),
      render: (row) => formatCurrency(row.current_mrr_usd),
    },
    numericColumn('premium_users', 'Premium', (row) => row.premium_users, 'users'),
    {
      ...numericColumn('premium_share_pct', 'Premium share', (row) => row.premium_share_pct, 'percent'),
      render: (row) => formatPercent(row.premium_share_pct),
    },
  ]

  const churnColumns: Column<(typeof churnReasons.rows)[number]>[] = [
    {
      key: 'month',
      header: 'Month',
      value: (row) => row.month,
      render: (row) => formatMonthLabel(row.month),
      className: 'font-medium',
    },
    { key: 'reason', header: 'Reason', value: (row) => row.reason },
    {
      key: 'churn_type',
      header: 'Type',
      value: (row) => row.churn_type,
      render: (row) => (
        <Badge variant={row.churn_type === 'involuntary' ? 'warning' : 'secondary'}>
          {row.churn_type}
        </Badge>
      ),
    },
    numericColumn('cancellations', 'Cancellations', (row) => row.cancellations),
    {
      ...numericColumn('pct_of_month', 'Share of month', (row) => row.pct_of_month, 'percent'),
      render: (row) => formatPercent(row.pct_of_month),
    },
    {
      ...numericColumn('mrr_lost_usd', 'MRR lost', (row) => row.mrr_lost_usd, 'usd'),
      render: (row) => <span className="text-destructive">{formatCurrency(row.mrr_lost_usd)}</span>,
    },
    {
      ...numericColumn('avg_mrr_lost_usd', 'Mean MRR lost', (row) => row.avg_mrr_lost_usd, 'usd'),
      render: (row) => formatCurrency(row.avg_mrr_lost_usd),
    },
    numericColumn('avg_tenure_days', 'Mean tenure', (row) => row.avg_tenure_days),
    numericColumn('median_tenure_days', 'Median tenure', (row) => row.median_tenure_days),
    numericColumn('churned_within_30d', 'Within 30 days', (row) => row.churned_within_30d),
    {
      ...numericColumn('early_churn_pct', 'Early churn', (row) => row.early_churn_pct, 'percent'),
      render: (row) => formatPercent(row.early_churn_pct),
    },
  ]

  const riskColumns: Column<(typeof risk.rows)[number]>[] = [
    {
      key: 'user_id',
      header: 'User',
      value: (row) => row.user_id,
      align: 'right',
      className: 'font-medium tabular',
    },
    {
      key: 'risk_band',
      header: 'Band',
      value: (row) => row.risk_band,
      render: (row) => (
        <Badge
          // `negative` and `warning` rather than a severity scale of their own: the badge
          // palette has no `destructive` token, and these two are the only ones that read as
          // "bad" and "worth attention". A medium band stays neutral.
          variant={
            row.risk_band === 'critical'
              ? 'negative'
              : row.risk_band === 'high'
                ? 'warning'
                : 'secondary'
          }
        >
          {row.risk_band}
        </Badge>
      ),
    },
    numericColumn('risk_score', 'Score', (row) => row.risk_score),
    {
      key: 'primary_driver',
      header: 'Primary driver',
      value: (row) => row.primary_driver,
      render: (row) => <span className="capitalize">{row.primary_driver}</span>,
    },
    // The five components of the score. They sum to it exactly — see the module docstring.
    numericColumn('recency_points', 'Recency', (row) => row.recency_points),
    numericColumn('frequency_points', 'Frequency', (row) => row.frequency_points),
    numericColumn('engagement_points', 'Engagement', (row) => row.engagement_points),
    numericColumn('volume_points', 'Volume', (row) => row.volume_points),
    numericColumn('tenure_points', 'Tenure', (row) => row.tenure_points),
    numericColumn(
      'days_since_last_active',
      'Days since active',
      (row) => row.days_since_last_active,
    ),
    numericColumn('active_days_28d', 'Active days (28d)', (row) => row.active_days_28d),
    numericColumn('watch_hours_28d', 'Watch hours (28d)', (row) => row.watch_hours_28d, 'hours'),
    numericColumn('total_sessions', 'Sessions', (row) => row.total_sessions, 'sessions'),
    {
      // A 0-1 fraction here too, named without the `_pct` suffix that would imply otherwise.
      ...numericColumn('completion_rate', 'Completion', (row) => row.completion_rate),
      render: (row) => formatRatioAsPercent(row.completion_rate),
    },
    numericColumn('tenure_days', 'Tenure days', (row) => row.tenure_days),
    {
      key: 'has_active_subscription',
      header: 'Subscribed',
      value: (row) => row.has_active_subscription,
      render: (row) =>
        row.has_active_subscription ? (
          <span className="text-muted-foreground">yes</span>
        ) : (
          <span className="text-muted-foreground">no</span>
        ),
    },
    {
      ...numericColumn('mrr_at_risk_usd', 'MRR at risk', (row) => row.mrr_at_risk_usd, 'usd'),
      render: (row) => formatCurrency(row.mrr_at_risk_usd),
    },
    {
      ...numericColumn(
        'lifetime_revenue_usd',
        'Lifetime revenue',
        (row) => row.lifetime_revenue_usd,
        'usd',
      ),
      render: (row) => formatCurrency(row.lifetime_revenue_usd),
    },
    { key: 'country', header: 'Country', value: (row) => row.country },
    { key: 'channel', header: 'Channel', value: (row) => row.channel },
    { key: 'persona', header: 'Persona', value: (row) => row.persona },
    {
      key: 'signup_date',
      header: 'Signed up',
      value: (row) => row.signup_date,
      render: (row) => formatDate(row.signup_date),
    },
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="Country ranking"
        definition="Every country by users, watch time and revenue, as of the window's end date. This is a snapshot rather than a period measurement, so it takes an as-of date and not a range."
        {...countries.boundary}
        emptyMessage={`No country in the dataset has ${minCountrySize} users, so all of them were filtered out. Lower the minimum to include smaller markets.`}
        actions={
          <Select
            value={String(minCountrySize)}
            onValueChange={(value) => setMinCountrySize(Number(value))}
          >
            <SelectTrigger className="h-7 w-40 text-xs" aria-label="Minimum users per country">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COUNTRY_SIZE_FLOORS.map((size) => (
                <SelectItem key={size} value={String(size)} className="text-xs">
                  {size === 30 ? '30+ users (default)' : `${size}+ users`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        onExport={() =>
          exportRows('geo-country-ranking', countries.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          {/* Grouped, not stacked: three shares of three different wholes, each summing to 100%
              across countries. */}
          <CategoryBarChart
            data={countries.rows}
            categoryKey="country"
            unit="percent"
            categoryWidth={130}
            height={Math.max(240, countries.rows.length * 30)}
            series={[
              { key: 'share_of_users_pct', label: 'Share of users' },
              { key: 'share_of_watch_pct', label: 'Share of watch hours' },
              { key: 'share_of_revenue_pct', label: 'Share of revenue' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            The revenue index is the gap between the last two bars expressed as a multiple: India
            holds the largest share of users and watch time but earns a fraction of the revenue,
            while the United States is the reverse. That divergence is the dataset&apos;s tier
            design — a high-volume market and a high-ARPU one are different businesses. ARPPU is
            blank for countries with no payers, where a per-payer figure has no denominator.
          </p>

          <DataTable
            rows={countries.rows}
            columns={countryColumns}
            rowKey={(row) => row.country}
            maxHeight="26rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Devices at signup"
        definition="Each user counted once, by the device they signed up on. Carries revenue and conversion, because a user has exactly one signup device to attribute them to."
        {...devices.boundary}
        isEmpty={signupDevices.length === 0 && devices.boundary.isEmpty}
        onExport={() =>
          exportRows('geo-device-breakdown-signup', signupDevices, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={signupDevices}
            categoryKey="platform"
            unit="percent"
            categoryWidth={110}
            height={Math.max(200, signupDevices.length * 28)}
            formatCategory={(value) => value}
            series={[
              { key: 'share_of_users_pct', label: 'Share of users' },
              { key: 'share_of_watch_pct', label: 'Share of watch hours' },
            ]}
          />

          <DataTable
            rows={signupDevices}
            columns={signupDeviceColumns}
            rowKey={(row) => `signup-${row.form_factor}-${row.platform}`}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Devices in use"
        definition="Sessions and watch time by the device they happened on. A user appears once per device they use, so these user counts overlap and cannot be added to a population total."
        {...devices.boundary}
        isEmpty={usageDevices.length === 0 && devices.boundary.isEmpty}
        onExport={() =>
          exportRows('geo-device-breakdown-usage', usageDevices, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={usageDevices}
            categoryKey="platform"
            unit="hours"
            hideLegend
            categoryWidth={110}
            height={Math.max(200, usageDevices.length * 28)}
            formatCategory={(value) => value}
            series={[{ key: 'watch_hours', label: 'Watch hours' }]}
          />

          <p className="text-2xs text-muted-foreground">
            This table and the one above it are separate on purpose. These rows count a user once
            for every device they watch on, so they total well over the actual population — a
            person with a phone and a TV is in both rows. Revenue and conversion are absent here
            rather than zero: money attaches to a user, and a user in this table has no single
            device to attribute it to. The share column is a share of these overlapping counts,
            which is why it sums to 100% without describing a headcount.
          </p>

          <DataTable
            rows={usageDevices}
            columns={usageDeviceColumns}
            rowKey={(row) => `usage-${row.form_factor}-${row.platform}`}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="RFM segments"
        definition="Users grouped by how recently, how often and how much they watch. Deciles are relative to the whole population, so a segment's position is always comparative."
        {...rfm.boundary}
        onExport={() => exportRows('users-rfm-segments', rfm.rows)}
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={rfm.rows}
            categoryKey="rfm_segment"
            unit="percent"
            categoryWidth={150}
            height={Math.max(200, rfm.rows.length * 32)}
            formatCategory={(value) => humanize(value)}
            series={[
              { key: 'pct_of_users', label: 'Share of users' },
              { key: 'pct_of_revenue', label: 'Share of revenue' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            The two bars on the champions row are the finding: about a fifth of users produce
            better than four fifths of revenue. That concentration is the reason the at-risk
            high-value segment is worth watching separately from the merely lost — losing a
            champion and losing a dormant free user are not the same event. Note that this panel
            ignores the date window entirely: it describes every user on record as they are now.
          </p>

          <DataTable
            rows={rfm.rows}
            columns={rfmColumns}
            rowKey={(row) => row.rfm_segment}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Why subscriptions end"
        definition="Cancellations by stated reason each month, split into voluntary and involuntary. An involuntary churn is a failed payment rather than a decision to leave."
        {...churnReasons.boundary}
        onExport={() =>
          exportRows('churn-reason-mix', churnReasons.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <p className="text-2xs text-muted-foreground">
            This window contains {formatNumber(churnReasons.rows.length)}{' '}
            {churnReasons.rows.length === 1 ? 'row' : 'rows'} because almost nobody has cancelled
            yet — the subscriber base is three months old. A share of 100% here means one
            cancellation out of one, not a dominant reason. Treat this panel as a shape the query
            returns correctly rather than as a finding about churn.
          </p>

          <DataTable
            rows={churnReasons.rows}
            columns={churnColumns}
            rowKey={(row, index) => `${row.month}-${row.reason}-${index}`}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Churn risk scorecard"
        definition="Users scoring above the threshold, with the five components that make up their score. The primary driver names the largest single contributor."
        {...risk.boundary}
        emptyMessage={`No user scores ${minRiskScore} or above, so nobody is listed. Lower the threshold to widen the net.`}
        actions={
          <Select
            value={String(minRiskScore)}
            onValueChange={(value) => setMinRiskScore(Number(value))}
          >
            <SelectTrigger className="h-7 w-40 text-xs" aria-label="Minimum risk score">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RISK_SCORE_FLOORS.map((score) => (
                <SelectItem key={score} value={String(score)} className="text-xs">
                  {score === 30 ? 'Score 30+ (default)' : `Score ${score}+`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        onExport={() => exportRows('churn-risk-scorecard', risk.rows)}
      >
        <div className="space-y-3">
          <p className="text-2xs text-muted-foreground">
            The five point columns sum to the score exactly, which is what makes the breakdown
            worth showing: a score of 72 built mostly from recency is a dormant user, while the
            same score built from engagement is someone still visiting but abandoning what they
            start. Those need different responses, and the total alone cannot tell them apart.
            Like the RFM panel, this one ignores the date window — it is a current-state list.
          </p>

          <DataTable
            rows={risk.rows}
            columns={riskColumns}
            rowKey={(row) => String(row.user_id)}
            maxHeight="26rem"
          />
        </div>
      </ChartCard>
    </div>
  )
}
