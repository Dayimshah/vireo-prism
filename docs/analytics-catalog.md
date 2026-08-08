# Analytics catalogue

What the numbers mean, how the statistics are computed, and which findings the
dataset actually supports. Written against a live `small` profile, and every
figure below is labelled with the profile it came from.

> `seeder/config.py` says this document "quotes concrete numbers from the `medium`
> profile at seed 20240817". It does not. The database in front of me is `small`
> (600 users), which is what the 330-test suite was verified against, and
> re-seeding to `medium` to match a docstring would invalidate that verification
> for no analytical gain. Where a `medium` figure is quoted from `config.py` it is
> marked as a *claim from config*, not a measurement.

## Three percentage conventions

Mixing these is a factor-of-100 bug, and it is the single most likely way to
misread this API.

| Convention | Columns | Example |
| --- | --- | --- |
| Pre-multiplied percentage | everything ending `_pct` | `51.1` means 51.1% |
| 0–1 fraction | `completion_rate`, `avg_completion_rate` | `0.511` means 51.1% |
| 0–1 fraction | `traffic_allocation`, `observed_power` | `0.80` means 80% |

The frontend has one formatter per convention (`formatPercent`,
`formatRatioAsPercent`) and picking the wrong one produces a chart that is wrong
by two orders of magnitude while still rendering plausibly.

## A null is an undefined figure, never zero

The whole stack holds this line: the SQL returns `NULL`, the API serialises
`null`, the dashboard renders an em-dash. A missing figure and a measured zero are
different findings, and reporting the second when you have the first is a claim
about data you do not have.

**One documented exception.** The MRR waterfall's movement columns are
`SUM(delta) FILTER (...)`, and an aggregate over no rows is `NULL`. There, null
genuinely means "no revenue moved that way this month" and is summed as zero. At
the `small` profile `reactivation_mrr` is null for all 15 months, and every month
has at least three null movement columns. `zero_if_absent()` in
`tests/integration/test_query_values.py` is the one place in the test suite where
null-means-zero is the correct reading, and it is commented as such.

## Statistics

`app/services/stats.py` is self-contained — no scipy, no numpy. The distribution
functions are implemented directly (`normal_cdf`, `normal_quantile` by bisection
handing over to Newton, `student_t_cdf` via a regularised incomplete beta with a
continued fraction) and unit-tested against closed forms rather than against
recorded output: the Cauchy CDF at df=1, the published t-table, Wilson intervals
worked by hand.

### Constants

| Constant | Value | Why |
| --- | --- | --- |
| `DEFAULT_ALPHA` | 0.05 | Two-sided. Overridable per request via `?alpha=` |
| `DEFAULT_POWER` | 0.80 | Convention, not law, which is why it is an argument |
| `MIN_ARM_SIZE` | 30 | Below this the normal approximation is not trustworthy |

### Verdicts

`compare_proportions` returns one of four labels, in this order of precedence:

1. **`underpowered`** — either arm below `MIN_ARM_SIZE`. No verdict is offered,
   because the honest answer is "wait".
2. **`inconclusive`** — `p >= alpha`. A real result, not a failure: a flat test
   tells you to stop spending on the idea.
3. **`winner`** / **`loser`** — significant, split by the sign of the lift. Losses
   are reported rather than buried.

Note the precedence: an underpowered arm never reports a winner even if `p`
happens to fall below `alpha`. A small arm that clears significance has usually
done so by luck.

### Two approximations, stated

- **`observed_power`** is post-hoc power at the observed effect size, and returns
  exactly `0.0` when the arms are identical — there is no effect to have power
  against. It counts one tail only; the opposite-tail contribution is negligible
  for any effect worth reporting and including it would inflate power for a null
  effect. It is **not** used to decide significance, because post-hoc power is a
  monotone transform of the p-value and using it as a second criterion would be
  the same test counted twice.
- **`minimum_detectable_effect`** estimates the variance at the baseline rate,
  which makes it an approximation. Exact inversion would require solving for a
  lift that appears in the variance term as well as the numerator.
  `required_sample_size` is not subject to this — it knows both rates, so it uses
  both.

The two are therefore not exact inverses of each other, which is deliberate and
documented in both docstrings.

### Why MDE is reported at all

"Not significant" on its own is not a conclusion. "This test could not have
detected anything smaller than 17.9 percentage points" is. Every inconclusive
verdict carries an MDE for that reason.

## Experiments: what the seeded data can and cannot show

This is the most important section in this document, because the experiment page
is the one most likely to be misread as broken.

### Every verdict at the `small` profile is inconclusive or underpowered

Measured, `observation_end=2026-08-06`:

| Experiment | Metric | Control n | Variant n | Lift pp | p | Power | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `autoplay-preview-v2` | `trailer_to_start` | 15 | 12 | +20.00 | 0.299 | 0.186 | underpowered |
| `continue-watching-position` | `completion_rate` | 44 | 62 | −6.45 | 0.512 | 0.096 | inconclusive |
| `onboarding-genre-picker` | `day7_retention` | 48 | 65 / 56 | 0.00 | 1.000 | 0.000 | inconclusive |
| `paywall-copy-value-first` | `subscription_conversion` | 28 | 25 | −7.14 | 0.173 | 0.311 | underpowered |

That is arithmetic, not a defect. The planted lifts and what it would take to
detect them, at a 10% baseline:

| Planted lift | Required n per arm | Actual arms |
| --- | --- | --- |
| 8.4 pp (`autoplay`) | 268 | 15 / 12 |
| 6.2 pp (`continue-watching`) | 461 | 44 / 62 |
| 5.5 pp (`onboarding`) | 574 | 48 / 65 / 56 |
| 4.1 pp (`paywall`) | 986 | 28 / 25 |

And from the other direction — the smallest lift each arm size could resolve:

| Arm size | MDE at 10% baseline |
| --- | --- |
| 12 | 34.3 pp |
| 25 | 23.8 pp |
| 44 | 17.9 pp |
| 65 | 14.7 pp |

Planted effects of 4–8 pp against a detection floor of 15–34 pp. The arms are one
to two orders of magnitude too small, so two of the four even come out with the
wrong *sign*. The statistics layer is doing its job: it declines to call a winner
rather than reporting the noise as a finding. **To see the planted effects
recovered, seed `medium` or `large`.**

### Only four of eight declared experiments exist

`config.py` declares eight `EXPERIMENT_SPECS`; `experiments.py` seeds
`EXPERIMENT_SPECS[:count]`, and `count` is a profile field: `small` 4, `medium` 6,
`large` 8. Because the slice is in declaration order, the specs carrying the most
interesting *intended* outcomes are the ones that never appear at `small`:

| Spec | Planted effect | Profile needed |
| --- | --- | --- |
| `search-ranking-recency` | 0.0 — deliberate null result | `medium` |
| `player-skip-intro` | 0.0 — second deliberate null | `medium` |
| `push-weekly-digest` | +3.8 pp | `large` |
| `pricing-annual-nudge` | **−1.9 pp — deliberate regression** | `large` |

So the `loser` verdict is unreachable at `small` and `medium` by construction:
the only experiment planted to lose is the eighth.

### Known limitation: `day7_retention` is structurally zero

`onboarding-genre-picker` reports 0% in all three arms, p = 1.000, power exactly
0.0. This is not sampling noise — it is two individually correct decisions
interacting badly.

`experiment_variant_metrics.sql` counts **only post-assignment activity**, and its
comment explains why: including pre-enrolment activity would measure who the users
already were rather than what the treatment did. That is right for a causal
comparison. But the seeder assigns users to experiments 0–315 days after signup,
and `day7_retention` asks whether the user was active on day 7 *after signup*. For
a user enrolled on day 9, the day-7 row is excluded by the post-assignment filter
before it can be counted.

Measured: of 169 assigned users, **4** have day 7 falling on or after their
assignment date (1 control, 0 variant_a, 3 variant_b). The numerator is
structurally near-empty, so all three arms read 0%.

Both halves are defensible alone and the interaction is not. It is recorded here
rather than patched, because changing either the metric window or the assignment
timing means rewriting delivered generator or query code, and the honest fix is a
design decision rather than a tweak. The stats layer's behaviour is at least
correct in the face of it: identical arms yield power `0.0` and no verdict, not a
false null result.

### Metric binarisation, and what it costs

Every experiment metric is reduced to a **binary outcome per user**, which is what
makes a two-proportion test valid:

| `primary_metric` | Binary outcome |
| --- | --- |
| `subscription_conversion` | did the user ever pay |
| `completion_rate` | did the user complete at least one title |
| `day7_retention` | was the user active on day 7 |
| `sessions_per_user` | did the user exceed the median session count |
| `trailer_to_start` | did the user start a title after a trailer |
| `session_duration` | did the user exceed the median session length |

Binarising a continuous metric loses information — a proper analysis of
`sessions_per_user` would be a t-test on counts, and `compare_means` (Welch's,
unequal variances) exists in `stats.py` for exactly that. The gain is one uniform
test with one set of assumptions across every experiment, which is far easier to
defend than six bespoke tests. That is the trade, stated rather than hidden.

The two median-threshold metrics compute their median **across both arms
together**. A per-arm median would move with the treatment effect and pin each arm
at ~50% by construction, making the test structurally incapable of detecting
anything.

## Planted causal structure, and how much of it is recoverable

The seeder declares causes; the SQL in `app/sql/queries/` knows none of them and
only aggregates events. Where a dashboard finding matches a declared coefficient,
that is the analytics layer independently recovering the relationship.

How well that works is a function of sample size, and at 600 users it works for
large effects and not for small ones.

### Watch time → conversion: recovered cleanly

The dominant planted term is `CONVERSION_WATCH_HOURS_COEF = 0.78` per unit of
`log1p(watch_hours_14d)`, against `CONVERSION_INTERCEPT = -10.00`. Measured
conversion by watch-time quintile (NTILE over lifetime watch seconds, `small`):

| Quintile | Conversion |
| --- | --- |
| 1 (lowest) | 0.00% |
| 2 | 0.00% |
| 3 | 0.00% |
| 4 | 6.67% |
| 5 (highest) | 33.33% |

Monotone, with the top quintile converting at least 10 points above the bottom.
`/monetization/conversion-by-watch-decile` cuts the same relationship by decile
and shows the same shape. This is the one planted relationship strong enough to be
unambiguous at `small`, and `test_query_values.py` asserts it: monotone,
`top > bottom + 10`, `top > 15`.

*Claim from config, not measured here:* the `medium` figures are 2.6% for a light
user (1h/14d) rising to 76.7% for a very heavy one (40h/14d), a 50× spread, with
17.3% population conversion. Measured population conversion at `small` is **8.3%**
(50 payers of 600). Profile-dependent, and the smaller figure is not evidence of a
fault.

### Persona → churn: recovered, monotone across all seven populated personas

`base_churn_propensity` against observed churn (`small`):

| Persona | Churn propensity | Observed churn | Sessions/wk | Completion |
| --- | --- | --- | --- | --- |
| Churn Risk | 0.460 | 92.6% | 0.70 | 0.280 |
| Sports Fan | 0.140 | 75.0% | 2.10 | 0.610 |
| Casual Viewer | 0.180 | 71.7% | 1.20 | 0.440 |
| Movie Lover | 0.070 | 46.6% | 2.80 | 0.740 |
| Anime Fan | 0.090 | 41.8% | 4.60 | 0.790 |
| Binge Watcher | 0.040 | 26.5% | 5.40 | 0.820 |
| Premium Loyalist | 0.030 | 21.2% | 3.90 | 0.770 |

Ordered by observed churn, the propensity column is monotone apart from the
Sports Fan / Casual Viewer pair, which sit 3.3 points apart on a 0.04 propensity
difference — well inside noise at n = 600.

### New Explorer has zero users, and that is intentional

An eighth persona is declared but no stored user carries it. `personas.py`:
"Inside the first 30 days" is not a stable identity, so a New Explorer converts
into one of the other seven after `GRADUATION_DAYS = 30`, weighted by
`GRADUATION_TARGETS` toward Casual Viewer (0.37). The stored `persona_id` records
where they **ended up**, which is what a real analyst sees — nobody is labelled
"new" eighteen months in. Their early-life behaviour still reflects exploration,
so day-1 and day-7 retention for those users genuinely differs from their eventual
steady state.

Consequence for any query grouping by persona: **seven groups, not eight.** A
segmentation chart with a missing New Explorer bar is correct.

### Channel → conversion: partially recovered

The headline marketing claim is that Referral and organic bring users who pay
while Display and Paid Social do not. Measured against
`CONVERSION_CHANNEL_EFFECT` (`small`):

| Channel | Coefficient | Users | Conversion |
| --- | --- | --- | --- |
| Organic Search | +0.34 | 110 | 11.8% |
| Direct | +0.31 | 77 | 11.7% |
| Referral | +0.58 | 62 | 11.3% |
| Influencer | −0.21 | 46 | 10.9% |
| App Store Featured | +0.22 | 39 | 10.3% |
| Telco Bundle | −0.12 | 14 | 7.1% |
| Organic Social | +0.08 | 62 | 6.5% |
| Paid Social | −0.46 | 69 | 5.8% |
| Email | +0.16 | 32 | 3.1% |
| Paid Search | −0.27 | 68 | 2.9% |
| Display | −0.63 | 7 | 0.0% |
| Affiliate | −0.04 | 14 | 0.0% |

Spearman rank correlation between coefficient and observed conversion:
**ρ = 0.712** across all twelve, **ρ = 0.750** restricted to the nine channels with
n ≥ 30.

So the extremes recover — the top three observed channels all carry positive
coefficients, Display and Paid Search sit at the bottom as declared — but the
middle is noise. The four worst rank inversions are Influencer (n = 46), Affiliate
(n = 14), Email (n = 32) and Paid Social (n = 69); three of the four are among the
smallest arms in the table. At 7 users, Display's 0.0% is one coin flip from
looking like the best channel in the dataset.

Read the LTV:CAC quadrant chart as directional at `small`, not as a per-channel
ranking. `min_cohort_size` exists on the marketing routes for this reason.

## Retention: three definitions, and right-censoring

Three definitions are plotted together because the differences are large enough
to change a decision. At day 1 on the seeded data, classic retention reads 40.5%
and rolling reads 91.5%. Quoting classic day-1 retention as "retention"
understates it by more than half.

| Definition | Counts a user as retained on day N if they |
| --- | --- |
| classic (`nday`) | were active on exactly day N |
| rolling | were active on day N **or later** |
| unbounded | were active at any point on or after day N |

By construction `rolling >= classic` and `unbounded >= classic` at every shared
horizon, which `test_query_values.py` asserts.

**The cohort shrinks as the horizon grows, and that is correct.** Measured
denominators: 598 users at the day-1 horizon, 549 at day-7, 495 at day-30. A user
who signed up 10 days before `observation_end` cannot have a day-30 outcome yet,
so including them would put them in the denominator with no chance of appearing in
the numerator — inventing a decay that is an artefact of the window rather than a
property of the product. This is why `observation_end` is a separate parameter from
the date window on all ten cohort and retention routes.

A first version of the value assertions assumed all three definitions shared one
cohort. They do not, and the test was rewritten to assert per-horizon agreement
plus monotone censoring instead.

## Churn risk scoring

`risk_score` is the exact arithmetic sum of five component columns, all returned so
the UI can show *why* an account is at risk rather than only that it is:

`recency_points + frequency_points + engagement_points + volume_points + tenure_points`

| Band | Score |
| --- | --- |
| critical | ≥ 70 |
| high | ≥ 50 |
| medium | ≥ 30 |
| low | below 30 |

`primary_driver` names the largest component. The sum identity, the band
thresholds and the `primary_driver` selection are all asserted — the assertion for
the last one initially read a column name that does not exist (`primary_risk_factor`
rather than `primary_driver`), so it compared against `None`, matched nothing and
passed while checking nothing. It now asserts the column is present *before*
reading it.

## Funnel

Stage rates chain to the end-to-end rate: multiplying the per-stage conversion
rates reproduces the overall figure to within 0.0005 (measured 21.50 chained
against 21.51 direct — rounding at each stage, not a discrepancy).

`segment_by` on the funnel routes accepts `country`, `channel`, `persona`,
`form_factor`, `platform`, `premium`. Retention's `segment_by` accepts `country`,
`channel`, `persona`, `device`, `premium`. **`persona` is the only token valid in
both.** The funnel segments by the session's device, which carries `form_factor`
and `platform` as separate attributes; retention segments by the user's
registration device.

Passing an unrecognised token does not error. It falls through a `CASE` to a
single `all` bucket, so `segment_by=device` on a funnel route returns one row
labelled `all` — a successful-looking response that answers a different question
than the one asked. Two tests pin this: one asserts the vocabularies genuinely
differ, the other asserts the degradation is to `{"all"}` rather than an error, so
the behaviour is documented rather than merely tolerated.

## Stickiness

DAU/MAU as a percentage — scale-free, so it cannot be inflated by acquisition
alone.

The displayed `dau` and `mau` columns are `COALESCE(..., 0)`, but the ratio divides
the **raw** operands: `100.0 * dau.dau / NULLIF(mau.mau, 0)`. Since the `dau` CTE
only emits rows for positive counts, a displayed `dau` of 0 means the raw operand
was `NULL`, which makes `stickiness_pct` null. So `stickiness_pct IS NULL` if and
only if displayed `dau = 0` — provable from the SQL, and asserted as such. Rows
like `{dau: 0, mau: 5, stickiness_pct: null}` are consistent with that rule.

Arguably 0% is the more defensible rendering for a day with no active users, since
the numerator is genuinely measured as zero rather than absent. The delivered
query returns null and is left alone.

## What the value assertions do and do not prove

`tests/integration/test_query_values.py` closes the build plan's own open
limitation, which read: *"the queries are verified to EXECUTE and return plausible
shapes, not to return CORRECT numbers."*

Twenty-three assertions, all of them **identities and orderings** rather than
literals:

- the funnel's stage rates multiply to its end-to-end rate
- the MRR waterfall balances opening → closing, and chains month to month
- `risk_score` equals the sum of its five components; bands match thresholds
- rolling and unbounded retention are never below classic at a shared horizon
- conversion rises monotonically with watch-time quintile
- stickiness is exactly DAU/MAU, with a provable null characterisation

Pinning literal figures would fail on every reseed while proving nothing, because
the expected value would have been copied from the query under test. Relationships
survive a reseed and a profile change, and they are what would actually break if a
query were wrong.

What this does **not** prove: that the planted coefficients are recovered at the
`small` profile. Two of the four experiments come out with the wrong sign, and the
channel ranking is only ρ = 0.71. Those are sample-size facts about the profile,
recorded above rather than asserted in a test that a larger profile would then
fail differently.
