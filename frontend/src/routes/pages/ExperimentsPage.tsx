import { useMemo, useState } from 'react'

import { usePanel } from '@/api/panel'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { QueryBoundary } from '@/components/state/QueryBoundary'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { exportRows, type CsvValue } from '@/lib/csv'
import { formatDate } from '@/lib/dates'
import {
  EMPTY,
  formatNumber,
  formatPercent,
  formatRatioAsPercent,
  humanize,
  isAbsent,
  pluralize,
} from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * A/B tests: what was tried, what happened, and how much of it is signal.
 *
 * Three endpoints. `/experiments` lists the tests — it was added to the backend for this page,
 * because otherwise choosing an experiment would mean hardcoding keys in the client and the list
 * would go stale the moment a test was added. The other two take the key as a path parameter.
 *
 * Filtering an experiment invalidates it, and the API says so
 * --------------------------------------------------------
 * This is the most important thing on the page. Both experiment routes accept the full filter
 * set, and applying one returns `is_segmented: true`. That flag is not a note about scope — it
 * means the randomisation has been broken. An A/B test is valid because assignment to arms was
 * random with respect to everything else; filtering to, say, premium users in India selects on
 * attributes that correlate with the outcome, so the arms are no longer comparable and the
 * p-value no longer means what a p-value means. The page renders a prominent warning when the
 * flag comes back true, rather than a quiet badge, because a segmented result that looks like a
 * verdict is worse than no result.
 *
 * Three different scales in one response
 * ------------------------------------
 * * `control_rate_pct`, `variant_rate_pct`, `absolute_lift_pp`, `relative_lift_pct` and the
 *   interval bounds are **pre-multiplied** — `43.5` means 43.5%.
 * * `traffic_allocation` and `observed_power` are **0-1 fractions** — `0.6` means 60%, and the
 *   service that computes power documents its range as `[0, 1]`.
 * * `alpha` and `bonferroni_alpha` are **significance levels**, not percentages, and are shown
 *   as the decimals they are.
 *
 * Passing a fraction to `formatPercent` renders "0.6%" for a 60% allocation — plausible on
 * screen, wrong by two orders of magnitude. {@link formatRatioAsPercent} handles those two.
 *
 * The Bonferroni level is reported but not applied
 * ----------------------------------------------
 * `bonferroni_alpha` is what alpha *would* have to be to hold the family-wise error rate across
 * `comparisons` tests, and the schema is explicit that the verdicts use the unadjusted alpha. On
 * the three-arm experiment that is 0.025 against a nominal 0.05. So a variant marked significant
 * at p = 0.03 in a two-comparison test would not survive the correction, and nothing in the
 * verdict column would tell you. The page states both levels and says which one the verdicts
 * used.
 *
 * A null `z_statistic` is not a failure
 * -----------------------------------
 * The onboarding experiment has both arms at exactly 0%, so the pooled variance is zero: there is
 * no evidence of a difference and also no test to run. The API returns `z_statistic: null` with
 * `p_value: 1.0`, which is the honest encoding. A dash is rendered rather than a zero, because a
 * z of 0 would claim a computed result.
 *
 * `relative_lift_pct` is null only when *control* converted nobody
 * ------------------------------------------------------------
 * A relative lift needs a control rate in the denominator. `-100%` — as the paywall experiment
 * reports — is a real measurement meaning the treatment converted nobody while control did. The
 * two are different states and only the first is undefined.
 */

/** Verdicts the API can return. Kept as a table so the badge mapping is exhaustive. */
const VERDICT_VARIANT: Record<string, 'positive' | 'negative' | 'warning' | 'secondary'> = {
  winner: 'positive',
  loser: 'negative',
  underpowered: 'warning',
  inconclusive: 'secondary',
}

/** One comparison flattened for CSV — the nested intervals become four scalar columns. */
interface ComparisonExportRow extends Record<string, CsvValue> {
  variant: string
  control_n: number
  control_successes: number
  control_rate_pct: number
  control_interval_low: number
  control_interval_high: number
  variant_n: number
  variant_successes: number
  variant_rate_pct: number
  variant_interval_low: number
  variant_interval_high: number
  absolute_lift_pp: number
  /**
   * Both of these arrive as optional keys (`relative_lift_pct?: number | null`), so their schema
   * type is `number | null | undefined` — two encodings of the same absence, which is an artifact
   * of how the model serialises rather than a distinction that means anything. Normalised to
   * `null` where they are read, so the page carries the one convention the rest of the codebase
   * uses: null is an undefined figure.
   */
  relative_lift_pct: number | null
  z_statistic: number | null
  p_value: number
  alpha: number
  is_significant: boolean
  verdict: string
  observed_power: number
  intervals_overlap: boolean
}

export function ExperimentsPage() {
  const [chosenKey, setChosenKey] = useState<string | null>(null)

  const catalogue = usePanel('/experiments')
  const { activeFilterCount } = useFilters()

  // The list drives the default rather than a hardcoded key. `chosenKey` stays null until the
  // reader picks one, so the first render after the list lands selects the first experiment
  // without an effect that would flash an empty panel first.
  const experiments = catalogue.rows
  const selectedKey = chosenKey ?? experiments[0]?.experiment_key ?? null
  const selected = experiments.find((row) => row.experiment_key === selectedKey)

  const pathParams = selectedKey ? { experiment_key: selectedKey } : undefined
  const hasKey = selectedKey !== null

  const results = usePanel('/experiments/{experiment_key}/results', {
    ...(pathParams ? { pathParams } : {}),
    enabled: hasKey,
  })
  const variants = usePanel('/experiments/{experiment_key}/variants', {
    ...(pathParams ? { pathParams } : {}),
    enabled: hasKey,
  })

  // A value endpoint: one object, so `rows` is permanently empty and this reads `payload.data`.
  const outcome = results.payload?.data

  // Memoised for its identity rather than its cost. A bare `?? []` mints a new array on every
  // render the results panel has not resolved on, which re-runs the export mapping below and
  // hands DataTable a fresh `rows` prop each pass.
  const comparisons = useMemo(() => outcome?.variants ?? [], [outcome])

  const comparisonExport = useMemo<ComparisonExportRow[]>(
    () =>
      comparisons.map((test) => ({
        variant: test.variant,
        control_n: test.control_n,
        control_successes: test.control_successes,
        control_rate_pct: test.control_rate_pct,
        control_interval_low: test.control_interval.low,
        control_interval_high: test.control_interval.high,
        variant_n: test.variant_n,
        variant_successes: test.variant_successes,
        variant_rate_pct: test.variant_rate_pct,
        variant_interval_low: test.variant_interval.low,
        variant_interval_high: test.variant_interval.high,
        absolute_lift_pp: test.absolute_lift_pp,
        // `?? null` rather than passed through: see the interface. Absence has one encoding here.
        relative_lift_pct: test.relative_lift_pct ?? null,
        z_statistic: test.z_statistic ?? null,
        p_value: test.p_value,
        alpha: test.alpha,
        is_significant: test.is_significant,
        verdict: test.verdict,
        observed_power: test.observed_power,
        intervals_overlap: test.intervals_overlap,
      })),
    [comparisons],
  )

  const catalogueColumns: Column<(typeof experiments)[number]>[] = [
    {
      key: 'experiment_name',
      header: 'Experiment',
      value: (row) => row.experiment_name,
      className: 'font-medium',
    },
    {
      key: 'primary_metric',
      header: 'Primary metric',
      value: (row) => row.primary_metric,
      render: (row) => humanize(row.primary_metric),
    },
    {
      key: 'status',
      header: 'Status',
      value: (row) => row.status,
      render: (row) => (
        <Badge variant={row.status === 'running' ? 'positive' : 'secondary'}>{row.status}</Badge>
      ),
    },
    {
      key: 'started_on',
      header: 'Started',
      value: (row) => row.started_on,
      render: (row) => formatDate(row.started_on),
    },
    {
      // Null while an experiment is still running — not an unknown end date, an absent one.
      key: 'ended_on',
      header: 'Ended',
      value: (row) => row.ended_on,
      render: (row) => (row.ended_on ? formatDate(row.ended_on) : EMPTY),
    },
    {
      ...numericColumn('duration_days', 'Days', (row) => row.duration_days),
      render: (row) => (isAbsent(row.duration_days) ? EMPTY : formatNumber(row.duration_days)),
    },
    {
      // A 0-1 fraction. See the module docstring.
      ...numericColumn('traffic_allocation', 'Traffic', (row) => row.traffic_allocation),
      render: (row) => formatRatioAsPercent(row.traffic_allocation),
    },
    numericColumn('variant_count', 'Arms', (row) => row.variant_count),
    numericColumn('enrolled_users', 'Enrolled', (row) => row.enrolled_users, 'users'),
  ]

  const variantColumns: Column<(typeof variants.rows)[number]>[] = [
    {
      key: 'variant',
      header: 'Arm',
      value: (row) => row.variant,
      className: 'font-medium',
      render: (row) => (
        <span className="flex items-center gap-1.5">
          {row.variant}
          {row.is_control && (
            <Badge variant="muted" className="shrink-0">
              control
            </Badge>
          )}
        </span>
      ),
    },
    numericColumn('n', 'Users', (row) => row.n, 'users'),
    numericColumn('successes', 'Converted', (row) => row.successes),
    {
      ...numericColumn('rate_pct', 'Rate', (row) => row.rate_pct, 'percent'),
      render: (row) => formatPercent(row.rate_pct),
    },
  ]

  const comparisonColumns: Column<(typeof comparisons)[number]>[] = [
    {
      key: 'variant',
      header: 'Arm vs control',
      value: (row) => row.variant,
      className: 'font-medium',
    },
    {
      key: 'verdict',
      header: 'Verdict',
      value: (row) => row.verdict,
      render: (row) => (
        <Badge variant={VERDICT_VARIANT[row.verdict] ?? 'secondary'}>{row.verdict}</Badge>
      ),
    },
    numericColumn('variant_n', 'Users', (row) => row.variant_n, 'users'),
    numericColumn('variant_successes', 'Converted', (row) => row.variant_successes),
    {
      ...numericColumn('variant_rate_pct', 'Rate', (row) => row.variant_rate_pct, 'percent'),
      render: (row) => formatPercent(row.variant_rate_pct),
    },
    {
      // The Wilson interval, shown as a range. This is the honest expression of a rate measured
      // on a few dozen users — a bare 43.5% implies a precision these arms do not have.
      key: 'variant_interval',
      header: '95% interval',
      value: (row) =>
        `${formatNumber(row.variant_interval.low, 1)}–${formatNumber(row.variant_interval.high, 1)}`,
      align: 'right',
      render: (row) => (
        <span className="tabular">
          {formatPercent(row.variant_interval.low)} – {formatPercent(row.variant_interval.high)}
        </span>
      ),
    },
    {
      // Percentage *points*, not a percentage: the difference of two rates.
      ...numericColumn('absolute_lift_pp', 'Lift', (row) => row.absolute_lift_pp),
      render: (row) => (
        <span
          className={
            row.absolute_lift_pp > 0
              ? 'text-positive'
              : row.absolute_lift_pp < 0
                ? 'text-negative'
                : undefined
          }
        >
          {`${row.absolute_lift_pp > 0 ? '+' : ''}${formatNumber(row.absolute_lift_pp, 1)} pp`}
        </span>
      ),
    },
    {
      // Null only when control converted nobody. -100% is a real measurement.
      ...numericColumn('relative_lift_pct', 'Relative lift', (row) => row.relative_lift_pct),
      render: (row) => formatPercent(row.relative_lift_pct),
    },
    {
      // Null when both arms sit at the same extreme — no pooled variance, so no test.
      ...numericColumn('z_statistic', 'z', (row) => row.z_statistic),
      render: (row) => (isAbsent(row.z_statistic) ? EMPTY : formatNumber(row.z_statistic, 3)),
    },
    {
      ...numericColumn('p_value', 'p-value', (row) => row.p_value),
      render: (row) => formatNumber(row.p_value, 4),
    },
    {
      // A 0-1 fraction, and post-hoc: it separates "no effect" from "not enough data" and is
      // deliberately not part of the significance decision.
      ...numericColumn('observed_power', 'Observed power', (row) => row.observed_power),
      render: (row) => formatRatioAsPercent(row.observed_power),
    },
    {
      key: 'intervals_overlap',
      header: 'Intervals overlap',
      value: (row) => row.intervals_overlap,
      render: (row) => (
        <span className="text-muted-foreground">{row.intervals_overlap ? 'yes' : 'no'}</span>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="Experiments"
        definition="Every test on record, with how much traffic it took and how many users it enrolled. Choose one to see its results below."
        {...catalogue.boundary}
        actions={
          experiments.length > 0 && selectedKey ? (
            <Select value={selectedKey} onValueChange={setChosenKey}>
              <SelectTrigger className="h-7 w-56 text-xs" aria-label="Choose an experiment">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {experiments.map((row) => (
                  <SelectItem
                    key={row.experiment_key}
                    value={row.experiment_key}
                    className="text-xs"
                  >
                    {row.experiment_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : undefined
        }
        onExport={() => exportRows('experiments-catalogue', experiments)}
      >
        <DataTable
          rows={experiments}
          columns={catalogueColumns}
          rowKey={(row) => row.experiment_key}
        />
      </ChartCard>

      {selected && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">{selected.experiment_name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{selected.hypothesis}</p>
          </CardHeader>
          <CardContent className="space-y-2 text-2xs text-muted-foreground">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span>
                Primary metric{' '}
                <span className="text-foreground">{humanize(selected.primary_metric)}</span>
              </span>
              <span>
                {formatRatioAsPercent(selected.traffic_allocation)} of eligible traffic
              </span>
              <span>{pluralize(selected.variant_count, 'arm')}</span>
              <span>{formatNumber(selected.enrolled_users)} enrolled</span>
              {/* `isAbsent` rather than `!== null`: the key is optional in the schema, so a
                  null-only test leaves `undefined` in the type and passes it to `pluralize`. */}
              {!isAbsent(selected.duration_days) && (
                <span>{pluralize(selected.duration_days, 'day')}</span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* The segmentation warning is a full-width panel rather than a badge. A filtered
          experiment is not a narrower result — it is an invalid one, and the reader has to see
          that before they read a verdict. */}
      {outcome?.is_segmented && (
        <Card className="border-warning/40 bg-warning-muted/30">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-warning">
              These results describe a segment, not the experiment
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs text-muted-foreground">
            <p>
              {pluralize(activeFilterCount, 'filter')} narrowed the population before the arms
              were compared. An A/B test is valid because assignment was random with respect to
              everything else; selecting on attributes that correlate with the outcome breaks that
              guarantee, so the arms below are no longer comparable and the p-values do not carry
              their usual meaning.
            </p>
            <p>
              Clear the filters to see the experiment&apos;s actual result. The API reports this as{' '}
              <code className="text-2xs">is_segmented</code> for exactly this reason — it is not a
              scope note.
            </p>
          </CardContent>
        </Card>
      )}

      <ChartCard
        title="Arms"
        definition="Each arm's enrolment and conversion on the primary metric, before any statistical comparison."
        {...variants.boundary}
        isWaiting={variants.boundary.isWaiting || !hasKey}
        onExport={() => exportRows(`experiment-${selectedKey ?? 'none'}-variants`, variants.rows)}
      >
        <DataTable
          rows={variants.rows}
          columns={variantColumns}
          rowKey={(row) => row.variant}
        />
      </ChartCard>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div className="min-w-0">
            <CardTitle className="text-sm">Statistical comparison</CardTitle>
            {outcome && (
              <p className="mt-1 text-xs text-muted-foreground">
                Two-sided proportion tests against{' '}
                <span className="text-foreground">{outcome.control_variant}</span>, over{' '}
                {formatNumber(outcome.total_n)} users in{' '}
                {pluralize(outcome.comparisons, 'comparison')}.
              </p>
            )}
          </div>
          {outcome && (
            <Badge variant={outcome.has_winner ? 'positive' : 'secondary'} className="shrink-0">
              {outcome.has_winner ? 'winner found' : 'no winner'}
            </Badge>
          )}
        </CardHeader>

        <CardContent>
          <QueryBoundary
            {...results.boundary}
            isWaiting={results.boundary.isWaiting || !hasKey}
            isEmpty={outcome !== undefined && comparisons.length === 0}
            emptyMessage="This experiment has no variant arms to compare against its control."
            skeletonRows={4}
          >
            <div className="space-y-3">
              <DataTable
                rows={comparisons}
                columns={comparisonColumns}
                rowKey={(row) => row.variant}
              />

              {outcome && (
                <div className="space-y-1.5 text-2xs text-muted-foreground">
                  <p>
                    Verdicts use an alpha of {formatNumber(outcome.alpha, 3)}.
                    {outcome.comparisons > 1 && (
                      <>
                        {' '}
                        With {pluralize(outcome.comparisons, 'comparison')} against one control,
                        holding the family-wise error rate at that level would require{' '}
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="cursor-help underline decoration-dotted">
                              {formatNumber(outcome.bonferroni_alpha, 4)}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p className="max-w-xs">
                              The Bonferroni level: alpha divided by the number of comparisons.
                              Testing several variants against one control inflates the chance that
                              at least one looks significant by luck.
                            </p>
                          </TooltipContent>
                        </Tooltip>{' '}
                        instead. The API reports that level but does not apply it, so a variant
                        significant at the unadjusted alpha may not survive the correction.
                      </>
                    )}
                  </p>
                  <p>
                    Observed power is post-hoc — it separates a genuinely flat result from one with
                    too little data, and is deliberately not part of the significance decision,
                    because post-hoc power is a transform of the p-value and testing against it
                    would count the same evidence twice. Non-overlapping intervals imply
                    significance; overlapping ones do not rule it out.
                  </p>
                  {outcome.observation_end && (
                    <p>
                      Outcomes counted up to {formatDate(outcome.observation_end)}, holding the
                      follow-up period equal across arms.
                    </p>
                  )}
                </div>
              )}
            </div>
          </QueryBoundary>
        </CardContent>
      </Card>

      {comparisonExport.length > 0 && (
        <div className="flex justify-end">
          <button
            type="button"
            className="text-2xs text-muted-foreground underline decoration-dotted hover:text-foreground"
            onClick={() =>
              exportRows(`experiment-${selectedKey ?? 'none'}-comparisons`, comparisonExport)
            }
          >
            Export comparison table as CSV
          </button>
        </div>
      )}
    </div>
  )
}
