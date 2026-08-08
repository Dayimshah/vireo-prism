"""Value assertions: the queries return *correct numbers*, not merely rows.

This file closes the build plan's own open limitation
----------------------------------------------------
The plan states it plainly:

    Open limitation: the queries are verified to EXECUTE and return plausible
    shapes, not to return CORRECT numbers. Value assertions are Phase 12 work.

``test_queries_execute.py`` is the "executes" half. Everything here is the other
half, and it is a different kind of test: instead of asking whether a query ran,
each one recomputes a relationship the SQL is supposed to satisfy and compares.

Why these five, and not "assert the numbers"
-------------------------------------------
Pinning literals — ``dau == 412`` — would produce a suite that fails on every
reseed while proving nothing about correctness, because the expected value would
have been copied from the same query it is checking. So each test asserts an
**identity or an ordering that must hold whatever the data is**:

* a funnel's stage rates must multiply to its end-to-end rate,
* an MRR waterfall's movements must balance opening to closing,
* a composite score must equal the sum of its components,
* rolling retention must be ≥ classic retention at every horizon,
* conversion must rise with watch time, because the generator planted that.

The last one is the only test here that reads ``core`` tables directly rather
than going through a registered query. It is deliberate: the causal signal is a
property of the *dataset*, and the whole point of the simulation is that a model
fitted to it can recover a relationship that was actually put there. If that
signal is ever flattened by a seeder change, every analytical query keeps
returning tidy plausible numbers and the project silently loses its reason to
exist.

Rounding
--------
The SQL rounds percentages to 2 decimal places, so a chained product of rounded
rates cannot equal a separately-rounded end-to-end rate exactly. Tolerances below
are stated per assertion with the reason, never widened just to get to green.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests.integration.conftest import Fetch

pytestmark = pytest.mark.integration

#: The five point columns that make up ``risk_score``.
RISK_COMPONENTS = (
    "recency_points",
    "frequency_points",
    "engagement_points",
    "volume_points",
    "tenure_points",
)

#: The signed movement columns of the MRR waterfall, in waterfall order.
MRR_MOVEMENTS = (
    "new_mrr",
    "reactivation_mrr",
    "expansion_mrr",
    "contraction_mrr",
    "churn_mrr",
)


def zero_if_absent(value: Any) -> Decimal:
    """Return ``value`` as a ``Decimal``, treating ``NULL`` as zero.

    **The one place in this project where null-means-zero is correct.** Everywhere
    else a ``NULL`` is an undefined figure and the frontend renders it as an
    em-dash rather than a 0 — that discipline is what stops a missing measurement
    from being read as a real one.

    The MRR waterfall is the documented exception. Its movement columns are
    ``SUM(delta) FILTER (WHERE movement = '...')``, and an aggregate over no rows
    is ``NULL`` rather than ``0``. Here that genuinely does mean "no revenue moved
    that way this month" — an arithmetic zero, not an unknown. Measured against
    the seeded dataset: ``reactivation_mrr`` is ``NULL`` for all 15 months and
    every month has at least three ``NULL`` movements, so a balance check that
    did not do this would propagate ``NULL`` and assert nothing at all.
    """
    return Decimal(0) if value is None else Decimal(value)


# ---------------------------------------------------------------------------
# 1. Funnel identities
# ---------------------------------------------------------------------------


async def test_funnel_stage_rates_multiply_to_the_end_to_end_rate(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """The chain rule: successive stage rates compose to the top-of-funnel rate.

    ``pct_of_previous`` is each step's conversion from the one before it, and
    ``pct_of_entry`` is its conversion from step 1. Multiplying the former across
    every step must reproduce the latter at the final step, because both are
    computed from the same session counts by different window functions — a bug in
    either ``FIRST_VALUE`` or ``LAG`` breaks the identity.

    Tolerance: each factor is rounded to 2dp before multiplication, so error
    accumulates over five factors. 0.05pp is comfortably tighter than any real
    defect and looser than the rounding. Measured: 21.50 chained vs 21.51 direct.
    """
    rows = await fetch("funnel/funnel_discovery_to_watch", **window)
    assert len(rows) >= 2, "a funnel needs at least two steps to have an identity"

    product = Decimal(1)
    for row in rows[1:]:
        assert row["pct_of_previous"] is not None, row["step_name"]
        product *= Decimal(row["pct_of_previous"]) / Decimal(100)

    end_to_end = Decimal(rows[-1]["pct_of_entry"]) / Decimal(100)
    assert product == pytest.approx(end_to_end, abs=0.0005)


async def test_the_first_funnel_step_has_no_previous_step(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """Step 1's ``pct_of_previous`` is ``NULL``, and that is the correct answer.

    ``LAG`` over the first row has nothing to return. Rendering that as 0% would
    claim total drop-off into the funnel's own entry point; rendering it as 100%
    would invent a step that does not exist. The API returns ``NULL`` and the
    frontend prints an em-dash — this pins that contract at the SQL boundary.
    """
    rows = await fetch("funnel/funnel_discovery_to_watch", **window)
    first = rows[0]

    assert first["step_order"] == 1
    assert first["pct_of_previous"] is None
    assert first["dropped_from_previous"] is None
    # But its share of entry is definitionally 100%, not null.
    assert Decimal(first["pct_of_entry"]) == Decimal("100.00")


async def test_funnel_sessions_never_increase_down_the_steps(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """A funnel is a subset chain: reaching step N+1 requires having reached N.

    The SQL builds each step with a conjunction of the previous predicates, so an
    increase would mean a step is being counted independently rather than as a
    continuation — which reads as a plausible funnel and is not one.
    """
    rows = await fetch("funnel/funnel_discovery_to_watch", **window)
    counts = [row["sessions"] for row in rows]

    assert counts == sorted(counts, reverse=True), counts
    # And the drop recorded at each step equals the actual difference.
    #
    # `pairwise` rather than `zip(rows, rows[1:])`: the pairing is over successive
    # elements, and saying so directly avoids both the slice copy and the question
    # of what `strict=` should be — there are five transitions between six steps,
    # so a strict zip could only ever raise.
    for previous, row in pairwise(rows):
        assert row["dropped_from_previous"] == previous["sessions"] - row["sessions"]


async def test_funnel_dropoff_and_conversion_are_complements(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """In the dropoff view, ``dropoff_pct + conversion_pct == 100``.

    Two ways of describing one transition. They are computed separately in the
    SQL, so the complement is a real check rather than a tautology.
    """
    rows = await fetch("funnel/funnel_step_dropoff", **window)
    assert rows

    for row in rows:
        total = Decimal(row["dropoff_pct"]) + Decimal(row["conversion_pct"])
        assert total == pytest.approx(Decimal(100), abs=0.02), row["from_step"]
        # And the counts agree with the rate.
        assert row["users_lost"] == row["from_count"] - row["to_count"]


# ---------------------------------------------------------------------------
# 2. The MRR waterfall balances
# ---------------------------------------------------------------------------


async def test_the_mrr_waterfall_balances_every_month(fetch: Fetch, window: dict[str, Any]) -> None:
    """Opening + new + reactivation + expansion + contraction + churn == closing.

    The defining property of a waterfall: the bars must land on the closing bar.
    Contraction and churn are stored **signed negative** precisely so this is a
    sum rather than a mix of additions and subtractions — the SQL comments say so,
    and this test is what holds them to it.

    Tolerance of one cent: each column is independently ``ROUND(..., 2)``, so six
    roundings can disagree with the rounded total in the last place.
    """
    rows = await fetch("monetization/mrr_movement_waterfall", **window)
    assert rows, "no MRR movement in the window"

    for row in rows:
        opening = zero_if_absent(row["opening_mrr"])
        movements = sum((zero_if_absent(row[key]) for key in MRR_MOVEMENTS), Decimal(0))
        closing = zero_if_absent(row["closing_mrr"])

        assert opening + movements == pytest.approx(
            closing, abs=Decimal("0.02")
        ), f"{row['month']}: {opening} + {movements} != {closing}"
        # net_change is the same sum reported independently.
        assert zero_if_absent(row["net_change_mrr"]) == pytest.approx(
            movements, abs=Decimal("0.02")
        ), row["month"]


async def test_each_month_opens_where_the_previous_closed(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """The waterfall chains across months with no gap.

    Computed from a self-join on the previous month's subscription state rather
    than by carrying a running total, so a boundary bug — an off-by-one month, a
    subscription counted in neither period — shows up here as a discontinuity.
    """
    rows = await fetch("monetization/mrr_movement_waterfall", **window)
    assert len(rows) >= 2, "need two months to check the chain"

    # Successive months, so `pairwise` states the intent directly.
    for earlier, later in pairwise(rows):
        assert zero_if_absent(later["opening_mrr"]) == pytest.approx(
            zero_if_absent(earlier["closing_mrr"]), abs=Decimal("0.02")
        ), (
            f"{earlier['month']} closed at {earlier['closing_mrr']}, "
            f"{later['month']} opened at {later['opening_mrr']}"
        )


async def test_mrr_movement_signs_are_directional(fetch: Fetch, window: dict[str, Any]) -> None:
    """Growth movements are never negative; loss movements are never positive.

    A sign error here would still balance — it would just move the same magnitude
    to the wrong side — so the balance test above cannot catch it.
    """
    rows = await fetch("monetization/mrr_movement_waterfall", **window)

    for row in rows:
        for key in ("new_mrr", "reactivation_mrr", "expansion_mrr"):
            assert zero_if_absent(row[key]) >= 0, f"{row['month']} {key}"
        for key in ("contraction_mrr", "churn_mrr"):
            assert zero_if_absent(row[key]) <= 0, f"{row['month']} {key}"
        # And MRR itself is never negative.
        assert zero_if_absent(row["opening_mrr"]) >= 0
        assert zero_if_absent(row["closing_mrr"]) >= 0


async def test_a_month_with_no_movement_of_a_kind_reports_null_not_zero(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """Documents the ``NULL`` this file's helper exists to absorb.

    ``SUM(...) FILTER`` over no matching rows is ``NULL``. Asserted rather than
    left implicit, because it is the reason :func:`zero_if_absent` exists and the
    reason a naive balance check silently passes while comparing nothing:
    ``None + Decimal`` raises, but ``pytest.approx`` against a ``None`` operand in
    a chain of additions would have been a much subtler wrong answer.
    """
    rows = await fetch("monetization/mrr_movement_waterfall", **window)

    absent = [key for row in rows for key in MRR_MOVEMENTS if row[key] is None]
    assert absent, (
        "expected at least one movement column to be NULL somewhere in the window; "
        "if the SQL now COALESCEs these to 0, zero_if_absent is dead code and this "
        "file's balance tests should be simplified"
    )


# ---------------------------------------------------------------------------
# 3. The risk score is the sum of its components
# ---------------------------------------------------------------------------


async def test_risk_score_equals_the_sum_of_its_five_components(fetch: Fetch) -> None:
    """The composite is exactly its parts — no hidden weighting.

    The scorecard exposes all five point columns so a user can see *why* an
    account is at risk. If the total were computed differently from the parts, the
    explanation shown next to the score would be a fiction.
    """
    rows = await fetch("churn/churn_risk_scorecard", min_risk_score=0)
    assert rows, "no at-risk users at min_risk_score=0"

    for row in rows:
        parts = sum(row[key] for key in RISK_COMPONENTS)
        assert row["risk_score"] == parts, {key: row[key] for key in RISK_COMPONENTS}


async def test_the_risk_band_matches_the_documented_thresholds(fetch: Fetch) -> None:
    """Critical ≥ 70, high ≥ 50, medium ≥ 30, low below that.

    The band drives the colour a user sees, so a boundary error changes the
    triage decision while every number on screen stays correct.
    """
    rows = await fetch("churn/churn_risk_scorecard", min_risk_score=0)

    for row in rows:
        score, band = row["risk_score"], row["risk_band"]
        if score >= 70:
            assert band == "critical", score
        elif score >= 50:
            assert band == "high", score
        elif score >= 30:
            assert band == "medium", score
        else:
            assert band == "low", score


async def test_the_dominant_risk_factor_is_the_largest_component(fetch: Fetch) -> None:
    """The named factor corresponds to the component with the most points.

    The SQL picks it with ``CASE GREATEST(...)``, which returns the *first* branch
    that equals the maximum — so on a tie the label is the earliest component, not
    an arbitrary one. Asserted as "is a maximum" rather than "is the maximum" so
    the test states the tie behaviour honestly instead of encoding one ordering.
    """
    rows = await fetch("churn/churn_risk_scorecard", min_risk_score=0)
    assert rows

    # The column is `primary_driver`. Asserted present before it is read: the
    # first version of this test used `row.get("primary_risk_factor")` — a name
    # that does not exist — so every comparison ran against `None`, matched
    # nothing, and the test passed while checking absolutely nothing.
    assert "primary_driver" in rows[0], sorted(rows[0])

    #: Label per component, in the CASE's own branch order. `tenure_points` is the
    #: ELSE branch rather than a WHEN, so it wins only when none of the other four
    #: equals the maximum.
    labels = {
        "recency_points": "dormant",
        "frequency_points": "visiting less",
        "engagement_points": "abandoning content",
        "volume_points": "low watch time",
        "tenure_points": "new account",
    }

    for row in rows:
        highest = max(row[key] for key in RISK_COMPONENTS)
        winners = {labels[key] for key in RISK_COMPONENTS if row[key] == highest}
        assert row["primary_driver"] in winners, {
            "driver": row["primary_driver"],
            "points": {key: row[key] for key in RISK_COMPONENTS},
        }


async def test_the_min_risk_score_filter_is_a_real_floor(fetch: Fetch) -> None:
    """Raising the floor only ever removes rows, and never below the threshold."""
    everyone = await fetch("churn/churn_risk_scorecard", min_risk_score=0)
    at_risk = await fetch("churn/churn_risk_scorecard", min_risk_score=50)

    assert len(at_risk) <= len(everyone)
    assert all(row["risk_score"] >= 50 for row in at_risk)


# ---------------------------------------------------------------------------
# 4. Retention definitions are ordered by construction
# ---------------------------------------------------------------------------


async def test_rolling_retention_is_never_below_classic_retention(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """The inequality that follows from the two definitions.

    Classic (N-day) retention counts users active *on exactly* day N. Rolling
    counts users active on day N *or any day after*. The second condition is
    strictly weaker, so rolling ≥ classic at every horizon — necessarily, for any
    dataset. An inversion means one of the two windows is wrong.

    Measured on the seeded data the gap is large (day 1: 91.47% rolling vs 40.47%
    classic), which is exactly why both are offered: quoting classic day-1
    retention as "retention" understates it by more than half.
    """
    classic = {row["day_n"]: row for row in await fetch("retention/retention_nday", **window)}
    rolling = {row["day_n"]: row for row in await fetch("retention/retention_rolling", **window)}

    shared = sorted(set(classic) & set(rolling))
    assert shared, "the two retention queries report no common horizons"

    for day in shared:
        assert rolling[day]["retained_users"] >= classic[day]["retained_users"], day
        assert Decimal(rolling[day]["retention_pct"]) >= Decimal(classic[day]["retention_pct"]), day


async def test_unbounded_retention_is_never_below_classic_retention(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """Same argument for the third definition, which is cumulative.

    Unbounded counts users who returned at any point up to day N, so it rises with
    the horizon while classic falls — the two cross at day 1, where the
    definitions coincide (40.47% in both, measured).
    """
    classic = {row["day_n"]: row for row in await fetch("retention/retention_nday", **window)}
    unbounded = {
        row["day_n"]: row for row in await fetch("retention/retention_unbounded", **window)
    }

    shared = sorted(set(classic) & set(unbounded))
    assert shared, "the two retention queries report no common horizons"

    for day in shared:
        assert unbounded[day]["retained_users"] >= classic[day]["retained_users"], day

    # Cumulative, so within a *fixed* cohort it cannot fall as the horizon widens.
    #
    # Restricted to horizons sharing one cohort size, which is the only place the
    # claim is true. An earlier version asserted it across all seven horizons and
    # failed: the day-60 and day-90 cohorts are smaller (549 and 495 against 598),
    # so their counts drop for a reason that has nothing to do with retention.
    # Even the *percentages* dip slightly there — 90.47 / 90.35 / 90.10 — because
    # the population being measured genuinely changes between horizons.
    largest = max(row["cohort_size"] for row in unbounded.values())
    fixed_cohort = [day for day in shared if unbounded[day]["cohort_size"] == largest]
    assert len(fixed_cohort) >= 2, "need two horizons on one cohort to check the trend"

    series = [unbounded[day]["retained_users"] for day in fixed_cohort]
    assert series == sorted(series), dict(zip(fixed_cohort, series, strict=False))


async def test_classic_retention_decays_with_the_horizon(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """Day-N retention falls as N grows.

    Not a mathematical necessity — a single day can buck the trend — but it is a
    property of this dataset and of any realistic one, and its failure would mean
    the engagement decay the generator applies has stopped working.
    """
    rows = await fetch("retention/retention_nday", **window)
    percentages = [Decimal(row["retention_pct"]) for row in rows]

    assert percentages == sorted(percentages, reverse=True), percentages


@pytest.mark.parametrize(
    "name",
    ["retention/retention_nday", "retention/retention_rolling", "retention/retention_unbounded"],
)
async def test_retention_percentages_agree_with_their_own_counts(
    name: str, fetch: Fetch, window: dict[str, Any]
) -> None:
    """``retention_pct == 100 * retained_users / cohort_size``.

    The percentage is what the chart plots and the counts are what the tooltip
    shows; a disagreement makes one of the two a lie. Also pins the convention:
    these columns are **pre-multiplied** (40.47 means 40.47%), not fractions.
    """
    rows = await fetch(name, **window)
    assert rows, name

    for row in rows:
        expected = Decimal(100) * Decimal(row["retained_users"]) / Decimal(row["cohort_size"])
        assert Decimal(row["retention_pct"]) == pytest.approx(expected, abs=Decimal("0.01")), row
        assert 0 <= Decimal(row["retention_pct"]) <= 100, row
        assert row["retained_users"] <= row["cohort_size"], row


async def test_all_three_retention_definitions_agree_on_the_cohort_at_each_horizon(
    fetch: Fetch, window: dict[str, Any]
) -> None:
    """The denominator must match *per horizon* — not across horizons.

    An earlier version of this test asserted a single cohort size for the whole
    series and failed, because the cohort legitimately **shrinks as the horizon
    grows**: 598 users at days 1-28, 549 at day 60, 495 at day 90 (measured).

    That is correct right-censoring, not a bug. Day-90 retention can only be
    computed for users who signed up at least 90 days before the observation end;
    including newer users would put them in the denominator with no chance of
    appearing in the numerator, which would drag every long-horizon rate down and
    invent a decay that is an artefact of the window.

    What must hold is that the three definitions agree with *each other* at each
    horizon, since they are plotted on one chart and compared point for point.
    """
    per_horizon: dict[int, dict[str, int]] = {}
    for name in (
        "retention/retention_nday",
        "retention/retention_rolling",
        "retention/retention_unbounded",
    ):
        for row in await fetch(name, **window):
            per_horizon.setdefault(row["day_n"], {})[name] = row["cohort_size"]

    assert per_horizon, "no retention horizons returned"

    for day, sizes in sorted(per_horizon.items()):
        assert len(set(sizes.values())) == 1, f"day {day}: cohort sizes disagree: {sizes}"

    # And the censoring is monotone: a longer horizon never has a larger cohort.
    ordered = [next(iter(sizes.values())) for _, sizes in sorted(per_horizon.items())]
    assert ordered == sorted(ordered, reverse=True), ordered


# ---------------------------------------------------------------------------
# 5. The planted causal signal survives
# ---------------------------------------------------------------------------


async def test_conversion_rises_with_watch_time(pg_engine: Any) -> None:
    """The relationship the whole simulation exists to contain.

    The generator makes conversion a function of engagement, so a model fitted to
    this dataset can *recover* something that was genuinely put there rather than
    an artefact of noise. Every analytical query would keep returning tidy numbers
    if that signal were flattened, so nothing else in this suite would notice.

    Raw SQL rather than a registered query: no endpoint exposes watch-time
    quintiles, and the claim is about the dataset, not about a query.

    Asserted as a property, not as figures. Measured here as 0.00 / 0.00 / 0.00 /
    6.67 / 33.33 percent — the build plan records 0 / 0 / 1.8 / 8.0 / 34.8 from a
    different quintile cut of the same generator. Same shape, different
    boundaries, which is exactly why the numbers themselves are not pinned.
    """
    from sqlalchemy import text

    statement = text(
        """
        WITH watched AS (
            SELECT u.user_id,
                   u.is_premium,
                   COALESCE(SUM(e.watch_seconds), 0) AS watch_seconds
            FROM core.users AS u
            LEFT JOIN core.events AS e
                   ON e.user_id = u.user_id AND e.watch_seconds IS NOT NULL
            GROUP BY u.user_id, u.is_premium
        ), ranked AS (
            SELECT NTILE(5) OVER (ORDER BY watch_seconds) AS quintile, is_premium
            FROM watched
        )
        SELECT quintile,
               COUNT(*)                                             AS users,
               ROUND(100.0 * COUNT(*) FILTER (WHERE is_premium)
                     / COUNT(*), 2)                                 AS conversion_pct
        FROM ranked
        GROUP BY quintile
        ORDER BY quintile
        """
    )

    async with pg_engine.connect() as conn:
        rows = [dict(row) for row in (await conn.execute(statement)).mappings()]

    assert len(rows) == 5, "expected five quintiles"
    rates = [Decimal(row["conversion_pct"]) for row in rows]

    # Monotone non-decreasing: more watching never means less converting.
    assert rates == sorted(rates), rates
    # And the signal is strong, not a rounding-scale wobble: the top quintile
    # converts by a wide margin over the bottom. A flattened generator would
    # still pass the ordering check above with all five rates near equal.
    assert rates[-1] > rates[0] + Decimal(10), rates
    assert rates[-1] > Decimal(15), rates


async def test_persona_conversion_ordering_matches_the_declared_coefficients(
    pg_engine: Any,
) -> None:
    """Persona conversion follows the coefficients stored in ``core.personas``.

    The personas are not decoration: each carries coefficients the generator uses,
    and ``Binge Watcher`` is the most engaged. If the ordering inverted, a
    dashboard segmented by persona would be reporting the opposite of the model
    that produced the data.

    Measured: Binge Watcher 31.33%, Premium Loyalist 18.18%, Anime Fan 10.45%,
    Movie Lover 8.74%, and Churn Risk / Casual Viewer / Sports Fan at 0%. The
    assertion is the ordering of the extremes, not the full ranking — the middle
    of the list is within sampling noise at 600 users and pinning it would make
    the test fragile without making it stronger.
    """
    from sqlalchemy import text

    statement = text(
        """
        SELECT p.name,
               COUNT(*)                                          AS users,
               ROUND(100.0 * COUNT(*) FILTER (WHERE u.is_premium)
                     / COUNT(*), 2)                              AS conversion_pct
        FROM core.users AS u
        JOIN core.personas AS p USING (persona_id)
        GROUP BY p.name
        """
    )

    async with pg_engine.connect() as conn:
        rows = [dict(row) for row in (await conn.execute(statement)).mappings()]

    by_persona = {row["name"]: Decimal(row["conversion_pct"]) for row in rows}
    assert "Binge Watcher" in by_persona, sorted(by_persona)

    best = max(by_persona.values())
    assert by_persona["Binge Watcher"] == best, by_persona
    # The least engaged personas convert at or near zero, and the spread is real.
    assert best > Decimal(15), by_persona
    assert min(by_persona.values()) < Decimal(5), by_persona


# ---------------------------------------------------------------------------
# Cross-cutting sanity
# ---------------------------------------------------------------------------


async def test_dau_never_exceeds_mau(fetch: Fetch, window: dict[str, Any]) -> None:
    """A day's actives are a subset of that month's, so DAU ≤ MAU always.

    Computed by separate queries over different windows, so this catches a window
    boundary error in either one.
    """
    # Each query names its own measure: `kpi/dau` returns `dau`, `kpi/mau`
    # returns `mau`. There is no shared `active_users` column.
    dau = {row["day"]: row["dau"] for row in await fetch("kpi/dau", **window)}
    mau = {row["day"]: row["mau"] for row in await fetch("kpi/mau", **window)}

    shared = sorted(set(dau) & set(mau))
    assert shared, "DAU and MAU report no common days"

    for day in shared:
        assert dau[day] <= mau[day], f"{day}: dau={dau[day]} > mau={mau[day]}"


async def test_stickiness_is_the_dau_mau_ratio(fetch: Fetch, window: dict[str, Any]) -> None:
    """Stickiness is DAU/MAU as a percentage, and stays within 0-100.

    Recomputed from the two component queries rather than trusted, because the
    ratio is what gets quoted and a wrong denominator is invisible in the number
    itself.
    """
    rows = await fetch("kpi/stickiness_dau_mau", **window)
    assert rows

    # The query returns all three columns, so the ratio is checkable against its
    # own operands rather than merely bounds-checked.
    assert {"dau", "mau", "stickiness_pct"} <= set(rows[0]), sorted(rows[0])

    for row in rows:
        # `stickiness_pct` is NULL exactly when `dau` is 0, and that is provable
        # from the SQL rather than merely observed. The two displayed columns are
        # `COALESCE(dau.dau, 0)` and `COALESCE(mau.mau, 0)`, but the ratio divides
        # the *raw* join operands: `100.0 * dau.dau / NULLIF(mau.mau, 0)`. The
        # `dau` CTE only emits rows where the count is positive, so a displayed
        # `dau` of 0 always means the join found no row and `dau.dau` was NULL —
        # which makes the whole expression NULL. And since DAU ≤ MAU, `mau` can
        # only be 0 when `dau` is too, so that branch adds no separate case.
        #
        # Worth naming: on such a day the ratio is arguably a defined 0% (nobody
        # of the 5 monthly actives showed up) rather than an unknown, so the
        # dashboard prints an em-dash where it could print 0%. Left as-is —
        # `kpi/stickiness_dau_mau.sql` is a delivered file and this is cosmetic —
        # but pinned here so the behaviour is a decision rather than an accident.
        if row["stickiness_pct"] is None:
            assert row["dau"] == 0, row
            continue

        assert row["dau"] > 0, row
        expected = Decimal(100) * Decimal(row["dau"]) / Decimal(row["mau"])
        assert Decimal(row["stickiness_pct"]) == pytest.approx(expected, abs=Decimal("0.01")), row
        assert Decimal(0) <= Decimal(row["stickiness_pct"]) <= Decimal(100), row
