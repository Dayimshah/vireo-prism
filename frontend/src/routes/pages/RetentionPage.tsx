import { useMemo, useState } from 'react'

import type { RetentionSegmentBy } from '@/api/endpoints'
import { usePanel } from '@/api/panel'
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
import { formatNumber, formatPercent, humanize } from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * Who comes back, measured three ways that do not nest inside each other.
 *
 * The three definitions are the point of this page
 * ----------------------------------------------
 * `/retention/nday`, `/retention/rolling` and `/retention/unbounded` return the identical
 * shape and answer different questions:
 *
 * * **Classic (N-day)** — active on exactly day N.
 * * **Rolling** — active on day N *or later*. Looks forward, so it is always ≥ classic.
 * * **Unbounded** — active at any point within the first N days. Looks backward, so it is
 *   also always ≥ classic.
 *
 * Neither rolling nor unbounded is ≥ the other; they are not nested. On the live dataset day
 * 1 reads classic 40.33 / rolling 91.50 / unbounded 40.33 — the two outer measures coincide
 * at day 1 and diverge after it. Plotting all three on one axis is the only way that
 * relationship is visible, and it is why they are not three separate cards.
 *
 * Unbounded retention is not monotonic, and that is correct
 * -------------------------------------------------------
 * Its `cohort_size` *shrinks* as `day_n` grows (600 → 551 → 497 on the live data), because a
 * user must have had N days of eligibility to be counted at all. So each `day_n` describes a
 * different population, and the percentages are not a curve down a single cohort. Monotonicity
 * holds only within a fixed `cohort_size` group. The table below carries `cohort_size` per
 * series for exactly this reason — without it, a rising unbounded figure reads as a bug.
 *
 * Two of these six panels need the cohort floor exposed
 * ---------------------------------------------------
 * `/retention/by-segment` and `/retention/curve-by-persona` both accept `min_cohort_size`, which
 * defaults to 30 — and on this dataset that default empties them:
 *
 * * **The persona curve returns nothing at all at 30**, and 13 rows at 20, 33 at 10. Left
 *   unfloored it is a permanently blank chart on a page whose docstring calls it the clearest
 *   expression of the dataset's design.
 * * **`by-segment` is emptied for three of its five dimensions** — country, channel and persona
 *   all return zero at 30, while device and premium return rows. That asymmetry is worse than a
 *   uniformly empty panel: a reader switching the selector from device to persona sees data
 *   become nothing and concludes personas have no retention, which is false.
 *
 * So the floor is a control here, exactly as it is on Cohorts, Monetization, Marketing and
 * Audience: initialised to the API's own default, with an empty state that names it as the cause.
 * The other four panels on this page do not accept the parameter and are unaffected.
 */

/** The five segments `/retention/by-segment` accepts. Not the funnel's list — those differ. */
const SEGMENT_OPTIONS: readonly RetentionSegmentBy[] = [
  'country',
  'channel',
  'persona',
  'device',
  'premium',
]

/** Floors offered by the control. 30 is the API's default and stays first. */
const COHORT_SIZE_FLOORS = [30, 20, 10, 5, 1] as const

/**
 * One row of the merged three-definition series.
 *
 * The index signature is `CsvValue`, not `unknown`: every field here is a scalar, so this is
 * both the honest type and the one that lets a merged row go to `exportRows` and to the chart
 * unchanged — `CsvValue` is a subtype of `unknown`, so the chart's looser bound still holds.
 */
interface CurveDatum extends Record<string, CsvValue> {
  day_n: number
  classic?: number | null
  rolling?: number | null
  unbounded?: number | null
  classic_cohort?: number
  rolling_cohort?: number
  unbounded_cohort?: number
}

/**
 * One row of the persona pivot — `week_n` plus one column per persona.
 *
 * The persona columns are only known at runtime, hence the index signature. `week_n` is
 * pinned as a `number` on top of it so the sort can subtract the two directly; reading it
 * back through the index signature would hand the comparator an `unknown` and require a
 * coercion that accepts anything.
 */
interface PersonaCurveDatum extends Record<string, CsvValue> {
  week_n: number
}

export function RetentionPage() {
  const { window } = useFilters()
  const [segmentBy, setSegmentBy] = useState<RetentionSegmentBy>('persona')
  const [minCohortSize, setMinCohortSize] = useState<number>(COHORT_SIZE_FLOORS[0])

  const nday = usePanel('/retention/nday')
  const rolling = usePanel('/retention/rolling')
  const unbounded = usePanel('/retention/unbounded')
  const bySegment = usePanel('/retention/by-segment', {
    extra: { segment_by: segmentBy, min_cohort_size: minCohortSize },
  })
  const byPersona = usePanel('/retention/curve-by-persona', {
    extra: { min_cohort_size: minCohortSize },
  })
  const resurrection = usePanel('/retention/resurrection')

  /** Shared by the two panels that accept the floor, so it is adjusted in one place. */
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
    `No group in this window reaches ${minCohortSize} users, so every one of them was filtered ` +
    `out. Lower the minimum cohort size to include smaller groups — their percentages move in ` +
    `large steps, which is the reason for the floor in the first place.`

  // Merged on `day_n`. All three return the same day set for the same window, but they are
  // three requests: keying by day rather than zipping by index means a series arriving with a
  // different length cannot pair the wrong figures together.
  const curves = useMemo<CurveDatum[]>(() => {
    const byDay = new Map<number, CurveDatum>()

    const put = (dayN: number): CurveDatum => {
      let entry = byDay.get(dayN)
      if (!entry) {
        entry = { day_n: dayN }
        byDay.set(dayN, entry)
      }
      return entry
    }

    for (const row of nday.rows) {
      const entry = put(row.day_n)
      entry.classic = row.retention_pct
      entry.classic_cohort = row.cohort_size
    }
    for (const row of rolling.rows) {
      const entry = put(row.day_n)
      entry.rolling = row.retention_pct
      entry.rolling_cohort = row.cohort_size
    }
    for (const row of unbounded.rows) {
      const entry = put(row.day_n)
      entry.unbounded = row.retention_pct
      entry.unbounded_cohort = row.cohort_size
    }

    return [...byDay.values()].sort((a, b) => a.day_n - b.day_n)
  }, [nday.rows, rolling.rows, unbounded.rows])

  const curveColumns: Column<CurveDatum>[] = [
    { key: 'day_n', header: 'Day', value: (row) => row.day_n, align: 'right' },
    {
      ...numericColumn('classic', 'Classic', (row) => row.classic, 'percent'),
      render: (row) => formatPercent(row.classic),
    },
    {
      ...numericColumn('rolling', 'Rolling', (row) => row.rolling, 'percent'),
      render: (row) => formatPercent(row.rolling),
    },
    {
      ...numericColumn('unbounded', 'Unbounded', (row) => row.unbounded, 'percent'),
      render: (row) => formatPercent(row.unbounded),
    },
    {
      // Carried because unbounded's denominator moves with `day_n`, so its percentages
      // describe different populations at different days. Hiding this column would make the
      // series look like a curve down one cohort, which it is not.
      ...numericColumn('unbounded_cohort', 'Unbounded cohort', (row) => row.unbounded_cohort, 'users'),
      render: (row) => formatNumber(row.unbounded_cohort),
    },
  ]

  const segmentColumns: Column<(typeof bySegment.rows)[number]>[] = [
    {
      key: 'segment',
      header: humanize(segmentBy),
      value: (row) => row.segment,
      className: 'font-medium',
    },
    { key: 'day_n', header: 'Day', value: (row) => row.day_n, align: 'right' },
    numericColumn('cohort_size', 'Cohort', (row) => row.cohort_size, 'users'),
    numericColumn('retained_users', 'Retained', (row) => row.retained_users, 'users'),
    {
      ...numericColumn('retention_pct', 'Retention', (row) => row.retention_pct, 'percent'),
      render: (row) => formatPercent(row.retention_pct),
    },
  ]

  // Pivoted into one series per persona so the curves are comparable on one axis. The x-axis
  // is `week_n` here, not `day_n` — this endpoint is weekly.
  const personaCurves = useMemo(() => {
    const byWeek = new Map<number, PersonaCurveDatum>()
    const personas = new Set<string>()

    for (const row of byPersona.rows) {
      personas.add(row.persona)
      let entry = byWeek.get(row.week_n)
      if (!entry) {
        entry = { week_n: row.week_n }
        byWeek.set(row.week_n, entry)
      }
      entry[row.persona] = row.retention_pct
    }

    return {
      data: [...byWeek.values()].sort((a, b) => a.week_n - b.week_n),
      series: [...personas].map((persona) => ({ key: persona, label: persona })),
    }
  }, [byPersona.rows])

  return (
    <div className="space-y-4">
      <ChartCard
        title="Three definitions of retention"
        definition="Classic is active on exactly day N. Rolling is active on day N or later. Unbounded is active within the first N days. Rolling and unbounded are both at least classic, and neither is inside the other."
        isPending={
          nday.boundary.isPending || rolling.boundary.isPending || unbounded.boundary.isPending
        }
        error={nday.boundary.error ?? rolling.boundary.error ?? unbounded.boundary.error}
        isWaiting={nday.boundary.isWaiting}
        isEmpty={curves.length === 0 && nday.boundary.isEmpty}
        hasFilters={nday.boundary.hasFilters}
        hasLanguageFilter={nday.boundary.hasLanguageFilter}
        onRetry={nday.boundary.onRetry}
        info={nday.info}
        onExport={() => exportRows('retention-curves', curves, { window: window ?? undefined })}
      >
        <div className="space-y-3">
          <TimeSeriesChart
            data={curves}
            xKey="day_n"
            xFormat="raw"
            unit="percent"
            series={[
              { key: 'classic', label: 'Classic (day N)' },
              { key: 'rolling', label: 'Rolling (day N or later)' },
              { key: 'unbounded', label: 'Unbounded (within N days)' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            Unbounded retention is not monotonic across days, and that is not a fault: its
            cohort shrinks as the day index grows, because a user needs N days of eligibility
            to be counted. Each day therefore describes a different population — the cohort
            column below carries the denominator.
          </p>

          <DataTable
            rows={curves}
            columns={curveColumns}
            rowKey={(row) => String(row.day_n)}
            maxHeight="18rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Retention by segment"
        definition="Classic retention split by one dimension. The segment list here is not the funnel's — these two endpoints expose different dimensions, so device appears here and form factor does not."
        {...bySegment.boundary}
        emptyMessage={floorEmptyMessage}
        actions={
          // Both controls, because either one can empty this panel: country, channel and persona
          // all return nothing at the default floor while device and premium return rows.
          <div className="flex items-center gap-2">
            <Select
              value={segmentBy}
              onValueChange={(value) => setSegmentBy(value as RetentionSegmentBy)}
            >
              <SelectTrigger className="h-7 w-36 text-xs" aria-label="Segment retention by">
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
            {floorControl}
          </div>
        }
        onExport={() =>
          exportRows(`retention-by-${segmentBy}`, bySegment.rows, { window: window ?? undefined })
        }
      >
        <DataTable
          rows={bySegment.rows}
          columns={segmentColumns}
          rowKey={(row, index) => `${row.segment}-${row.day_n}-${index}`}
          maxHeight="24rem"
        />
      </ChartCard>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Weekly curve by persona"
          definition="Retention by week for each behavioural persona. Persona is assigned at signup and drives watch behaviour, so these curves are the clearest expression of the dataset's causal design."
          {...byPersona.boundary}
          emptyMessage={floorEmptyMessage}
          actions={floorControl}
          onExport={() =>
            exportRows('retention-curve-by-persona', byPersona.rows, {
              window: window ?? undefined,
            })
          }
        >
          <TimeSeriesChart
            data={personaCurves.data}
            xKey="week_n"
            xFormat="raw"
            unit="percent"
            series={personaCurves.series}
          />
        </ChartCard>

        <ChartCard
          title="Resurrection"
          definition="Dormant users who came back, as a share of the dormant pool available to return. The rate is undefined rather than zero in a month with no dormant users."
          {...resurrection.boundary}
          onExport={() =>
            exportRows('retention-resurrection', resurrection.rows, {
              window: window ?? undefined,
            })
          }
        >
          <TimeSeriesChart
            data={resurrection.rows}
            xKey="month"
            xFormat="month"
            unit="percent"
            hideLegend
            series={[{ key: 'resurrection_rate_pct', label: 'Resurrection rate' }]}
          />
        </ChartCard>
      </div>
    </div>
  )
}
