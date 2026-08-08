import { useMemo, useState } from 'react'

import type { FunnelSegmentBy } from '@/api/endpoints'
import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { FunnelChart, type FunnelStep } from '@/components/charts/FunnelChart'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { exportRows } from '@/lib/csv'
import { formatDuration, formatNumber, formatPercent, humanize } from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * Where people fall out, in two funnels that count different things.
 *
 * The discovery funnel counts **sessions**; the subscribe funnel counts **users**. They are not
 * two views of one process and their percentages do not reconcile — a user with forty sessions
 * appears once in the second and forty times in the first. Each chart names its own noun for
 * that reason.
 *
 * The drop-off ranking covers four of the five transitions
 * ------------------------------------------------------
 * `/funnel/step-dropoff` returns exactly four rows, ending at Started → Completed. The discovery
 * funnel has a sixth step — Rated — and the Completed → Rated transition loses 7,986 sessions on
 * the live window, more than double the 3,018 of the worst transition the ranking *does* contain.
 * So `loss_rank: 1` means "the largest loss among these four", not "the largest loss in the
 * funnel", and a reader who takes it as the latter will optimise the wrong step. The card says
 * so, and the funnel above it shows the omitted transition in place.
 *
 * A rate over a small denominator is still a rate
 * --------------------------------------------
 * The subscribe funnel's window holds 114 signups, of whom 3 paid. 2.63% is arithmetically
 * correct and practically meaningless — three more conversions would nearly double it. The
 * denominator is stated beside the rate rather than left to be inferred from a bar width.
 *
 * `open_to_view_pct` spans two steps, not one
 * -----------------------------------------
 * `/funnel/by-segment` returns five counts but only three rates, and the first rate skips a
 * step: it is `viewed / opened`, so the `discovered` count sits between them uncredited. The
 * column is labelled for the span it actually measures.
 */

/** The six segments `/funnel/by-segment` accepts — not the retention list, which has `device`. */
const SEGMENT_OPTIONS: readonly FunnelSegmentBy[] = [
  'country',
  'channel',
  'persona',
  'form_factor',
  'platform',
  'premium',
]

/** The API's own floor for `/funnel/by-segment`, restated so the card can name it. */
const MIN_COHORT_SIZE = 30

export function FunnelsPage() {
  const { window } = useFilters()
  const [segmentBy, setSegmentBy] = useState<FunnelSegmentBy>('persona')

  const discovery = usePanel('/funnel/discovery-to-watch')
  const subscribe = usePanel('/funnel/signup-to-subscribe')
  const dropoff = usePanel('/funnel/step-dropoff')
  const timing = usePanel('/funnel/time-between-steps')
  const bySegment = usePanel('/funnel/by-segment', { extra: { segment_by: segmentBy } })

  const discoverySteps = useMemo<FunnelStep[]>(
    () =>
      discovery.rows.map((row) => ({
        label: row.step_name,
        count: row.sessions,
        pctOfEntry: row.pct_of_entry,
        pctOfPrevious: row.pct_of_previous,
        droppedFromPrevious: row.dropped_from_previous,
      })),
    [discovery.rows],
  )

  const subscribeSteps = useMemo<FunnelStep[]>(
    () =>
      subscribe.rows.map((row) => ({
        label: row.step_name,
        count: row.users,
        // Named `pct_of_signups` on this endpoint rather than `pct_of_entry`. Same meaning:
        // share of the first step.
        pctOfEntry: row.pct_of_signups,
        pctOfPrevious: row.pct_of_previous,
        droppedFromPrevious: row.dropped_from_previous,
      })),
    [subscribe.rows],
  )

  /** The entry step's count, for stating the denominator beside the final rate. */
  const signupCount = subscribe.rows[0]?.users
  const finalStep = subscribe.rows[subscribe.rows.length - 1]

  const dropoffColumns: Column<(typeof dropoff.rows)[number]>[] = [
    {
      key: 'transition',
      header: 'Transition',
      value: (row) => `${row.from_step} → ${row.to_step}`,
      className: 'font-medium',
    },
    numericColumn('from_count', 'Entered', (row) => row.from_count, 'sessions'),
    numericColumn('to_count', 'Continued', (row) => row.to_count, 'sessions'),
    numericColumn('users_lost', 'Lost', (row) => row.users_lost, 'sessions'),
    {
      ...numericColumn('dropoff_pct', 'Drop-off', (row) => row.dropoff_pct, 'percent'),
      render: (row) => formatPercent(row.dropoff_pct),
    },
    {
      ...numericColumn('conversion_pct', 'Continued', (row) => row.conversion_pct, 'percent'),
      render: (row) => formatPercent(row.conversion_pct),
    },
    {
      // Both ranks are 1-based over these four rows only. See the module docstring.
      ...numericColumn('loss_rank', 'Rank by count lost', (row) => row.loss_rank),
    },
    numericColumn('rate_rank', 'Rank by rate', (row) => row.rate_rank),
  ]

  const timingColumns: Column<(typeof timing.rows)[number]>[] = [
    {
      key: 'transition',
      header: 'Transition',
      value: (row) => row.transition,
      className: 'font-medium',
    },
    numericColumn('observations', 'Observations', (row) => row.observations),
    {
      // Rendered through `formatDuration`, which picks its own units: these span 2.5 seconds
      // to nearly two hours, and a single fixed unit would print either `0.0h` or `6972s`.
      ...numericColumn('p25_seconds', 'p25', (row) => row.p25_seconds),
      render: (row) => formatDuration(row.p25_seconds),
    },
    {
      ...numericColumn('median_seconds', 'Median', (row) => row.median_seconds),
      render: (row) => formatDuration(row.median_seconds),
    },
    {
      ...numericColumn('p90_seconds', 'p90', (row) => row.p90_seconds),
      render: (row) => formatDuration(row.p90_seconds),
    },
    numericColumn('median_minutes', 'Median (minutes)', (row) => row.median_minutes),
  ]

  const segmentColumns: Column<(typeof bySegment.rows)[number]>[] = [
    {
      key: 'segment',
      header: humanize(segmentBy),
      value: (row) => row.segment,
      className: 'font-medium',
    },
    numericColumn('opened', 'Opened', (row) => row.opened, 'sessions'),
    numericColumn('discovered', 'Discovered', (row) => row.discovered, 'sessions'),
    numericColumn('viewed', 'Viewed', (row) => row.viewed, 'sessions'),
    numericColumn('started', 'Started', (row) => row.started, 'sessions'),
    numericColumn('completed', 'Completed', (row) => row.completed, 'sessions'),
    {
      // `viewed / opened` — it spans opened → discovered → viewed, so the label names both
      // ends rather than implying a single hop.
      ...numericColumn('open_to_view_pct', 'Opened → viewed', (row) => row.open_to_view_pct, 'percent'),
      render: (row) => formatPercent(row.open_to_view_pct),
    },
    {
      ...numericColumn('view_to_start_pct', 'Viewed → started', (row) => row.view_to_start_pct, 'percent'),
      render: (row) => formatPercent(row.view_to_start_pct),
    },
    {
      ...numericColumn(
        'start_to_complete_pct',
        'Started → completed',
        (row) => row.start_to_complete_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.start_to_complete_pct),
    },
    {
      ...numericColumn('end_to_end_pct', 'End to end', (row) => row.end_to_end_pct, 'percent'),
      render: (row) => formatPercent(row.end_to_end_pct),
    },
  ]

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Discovery to watch"
          definition="One session's path from opening the app to rating what it watched. Counts sessions, not people — a session that never searched is not a user who never searches."
          {...discovery.boundary}
          isEmpty={discoverySteps.length === 0 && discovery.boundary.isEmpty}
          onExport={() =>
            exportRows('funnel-discovery-to-watch', discovery.rows, {
              window: window ?? undefined,
            })
          }
        >
          <div className="space-y-3">
            <FunnelChart steps={discoverySteps} noun="sessions" />

            <p className="text-2xs text-muted-foreground">
              The fall to <span className="font-medium">Rated</span> is the largest in the funnel
              and it is not a failure: rating is optional, and most sessions that finish something
              simply do not leave a rating. The steps before it are the ones a product change
              could move.
            </p>
          </div>
        </ChartCard>

        <ChartCard
          title="Signup to subscribe"
          definition="A cohort of new users from signup through to still paying. Counts people, so these percentages do not reconcile with the session funnel beside them."
          {...subscribe.boundary}
          isEmpty={subscribeSteps.length === 0 && subscribe.boundary.isEmpty}
          onExport={() =>
            exportRows('funnel-signup-to-subscribe', subscribe.rows, {
              window: window ?? undefined,
            })
          }
        >
          <div className="space-y-3">
            <FunnelChart steps={subscribeSteps} noun="users" />

            {finalStep && signupCount !== undefined && (
              <p className="text-2xs text-muted-foreground">
                Read the end of this funnel with its denominator in view:{' '}
                {formatNumber(finalStep.users)} of {formatNumber(signupCount)} users who signed up
                in this window are still paying. At that scale a rate moves several points per
                person, so treat {formatPercent(finalStep.pct_of_signups)} as an order of
                magnitude rather than a figure to compare across windows.
              </p>
            )}
          </div>
        </ChartCard>
      </div>

      <ChartCard
        title="Where the losses are largest"
        definition="Each transition in the discovery funnel by how many sessions it loses and what share that is. Ranked two ways, because the biggest rate and the biggest number are rarely the same step."
        {...dropoff.boundary}
        onExport={() =>
          exportRows('funnel-step-dropoff', dropoff.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          {/* Grouped bars of loss count against loss rate would share no scale, so the count is
              charted and the rate stays in the table. */}
          <CategoryBarChart
            data={dropoff.rows}
            categoryKey="from_step"
            unit="sessions"
            hideLegend
            categoryWidth={130}
            height={180}
            series={[{ key: 'users_lost', label: 'Sessions lost' }]}
          />

          <p className="text-2xs text-muted-foreground">
            Both ranks cover the {formatNumber(dropoff.rows.length)} transitions in this table only,
            which end at Started → Completed. The funnel above has one more step — Completed →
            Rated — and it loses more sessions than any transition ranked here. A rank of 1 means
            &ldquo;worst of these&rdquo;, not &ldquo;worst in the funnel&rdquo;.
          </p>

          <DataTable
            rows={dropoff.rows}
            columns={dropoffColumns}
            rowKey={(row) => String(row.step_order)}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="How long each step takes"
        definition="Time between consecutive steps, as percentiles rather than a mean. The p90 sits far above the median on every transition, which is what a long tail looks like when a mean would hide it."
        {...timing.boundary}
        onExport={() =>
          exportRows('funnel-time-between-steps', timing.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <p className="text-2xs text-muted-foreground">
            The gap between the median and the p90 is the reading here. Open to first search has a
            median under two minutes and a p90 near two hours: most searches happen immediately,
            and a minority happen much later in a long session. A mean would land between the two
            and describe neither.
          </p>

          <DataTable
            rows={timing.rows}
            columns={timingColumns}
            rowKey={(row) => String(row.step_order)}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Funnel by segment"
        definition={`End-to-end conversion split by one dimension, over segments with at least ${MIN_COHORT_SIZE} sessions. The segment list here is not the retention page's — these endpoints expose different dimensions.`}
        {...bySegment.boundary}
        actions={
          <Select
            value={segmentBy}
            onValueChange={(value) => setSegmentBy(value as FunnelSegmentBy)}
          >
            <SelectTrigger className="h-7 w-36 text-xs" aria-label="Segment funnel by">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SEGMENT_OPTIONS.map((option) => (
                <SelectItem key={option} value={option} className="text-xs">
                  {humanize(option)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        onExport={() =>
          exportRows(`funnel-by-${segmentBy}`, bySegment.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={bySegment.rows}
            categoryKey="segment"
            unit="percent"
            categoryWidth={130}
            height={Math.max(220, bySegment.rows.length * 30)}
            series={[
              { key: 'open_to_view_pct', label: 'Opened → viewed' },
              { key: 'view_to_start_pct', label: 'Viewed → started' },
              { key: 'start_to_complete_pct', label: 'Started → completed' },
              { key: 'end_to_end_pct', label: 'End to end' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            Grouped rather than stacked: these four rates share a scale but not a whole, and the
            end-to-end figure is the product of the other three, not their sum. Opened → viewed
            spans two steps — the discovered count sits inside it — so the three stage rates
            multiply to the end-to-end figure while covering four counts.
          </p>

          <DataTable
            rows={bySegment.rows}
            columns={segmentColumns}
            rowKey={(row) => row.segment}
            maxHeight="24rem"
          />
        </div>
      </ChartCard>
    </div>
  )
}
