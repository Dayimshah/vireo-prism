"""Response models for A/B test results.

Two endpoints: the raw per-variant counts, and those counts with a significance test
per variant. The counts model is an ordinary row model; the rest mirror the frozen
dataclasses in :mod:`app.services.stats` and :mod:`app.services.experiments`.

Computed properties are declared as fields
------------------------------------------
:class:`~app.services.stats.ProportionTest` exposes ``intervals_overlap`` as a
``@property``, and :class:`~app.services.experiments.ExperimentSummary` exposes
``comparisons``, ``bonferroni_alpha``, ``total_n`` and ``has_winner`` the same way. They
are not dataclass fields, so nothing would carry them across the wire unless declared
here.

They are declared, because each answers a question the caller would otherwise have to
re-derive — and ``bonferroni_alpha`` in particular is not something to leave to a
client. Three variants against one control at alpha=0.05 carry roughly a 14% chance of
at least one false positive; the correction is reported rather than applied, and hiding
the corrected threshold would turn a documented choice into a silent one.

``from_attributes=True`` lets Pydantic populate them by attribute lookup, which reads a
property exactly as it reads a field. Verified against the real dataclasses rather than
assumed.

Floats, not decimals
--------------------
Every statistic here is a ``float``, matching :mod:`app.services.stats`. That is a
departure from the rest of this package, where money and rates are ``Decimal``, and it
is the right way round: these values come from ``erfc`` and an incomplete beta
function, not from Postgres ``numeric``, so a ``Decimal`` annotation would claim an
exactness the arithmetic never had.

``p_value`` is floored at :data:`~app.services.stats.MIN_P_VALUE` rather than reaching
zero. A p-value of exactly zero is not a measurement, and the naive
``2 * (1 - cdf(|z|))`` that produces one is what the floor and the direct computation
exist to avoid.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from app.schemas.base import Number, PrismModel, RowModel
from app.services.stats import Verdict  # noqa: TC001 — a field annotation, resolved at runtime

if TYPE_CHECKING:
    from app.services.experiments import ExperimentSummary
    from app.services.stats import Interval, ProportionTest


class ExperimentSummaryRow(RowModel):
    """One experiment's definition, without any of its outcomes.

    What a picker needs in order to offer a choice. The other two endpoints in this
    namespace are keyed by slug, and before this existed a client had no way to obtain
    a slug it did not already know.

    Attributes:
        experiment_key: The experiment's stable slug — the value the other two
            endpoints take as a path parameter.
        experiment_name: Human-readable name.
        hypothesis: What the test was expected to do, as recorded when it was defined.
            Carried because a list of experiment names does not say what any of them
            were for, and the hypothesis is the one field that does.
        primary_metric: The metric the test was designed to move.
        status: Lifecycle status carried on ``core.experiments``.
        started_on: First day of the experiment. Required, not nullable: the column is
            ``NOT NULL`` and this query reads it straight from the table rather than
            through an outer join.
        ended_on: Last day, or ``None`` while the experiment is still running.
        traffic_allocation: Fraction of eligible users enrolled, in ``(0, 1]``.
        variant_count: How many arms the experiment declared, read from the definition
            rather than counted from assignments — an arm with nobody in it is still an
            arm the test declared.
        enrolled_users: Assignments on record. For orientation only: the per-variant
            endpoint recomputes its own ``n`` per arm after applying
            ``observation_end``, so this figure and the sum of the arms disagree by
            design whenever an observation cut-off is in force. Never use it as a
            denominator.
        duration_days: Length of the test in days, both endpoints inclusive, or
            ``None`` while it is still running. Null rather than measured-to-today: an
            unfinished test has no length yet, and counting to today would report a
            number that changes daily without anything having happened.
    """

    experiment_key: str
    experiment_name: str
    hypothesis: str
    primary_metric: str
    status: str
    started_on: date
    ended_on: date | None = None
    traffic_allocation: Number
    variant_count: int
    enrolled_users: int = Field(
        description="Orientation only. Not the denominator of any test — see the class docstring.",
    )
    duration_days: int | None = Field(
        default=None,
        description="Null while the experiment is still running.",
    )


class VariantMetricRow(RowModel):
    """Raw per-variant counts for one experiment, uninterpreted.

    What the SQL returns and no more. Exposed separately from the tested results so a
    reader can see the numbers a verdict was derived from — a table beside the chart,
    or a check that a surprising result is not a data problem.

    Attributes:
        experiment_key: The experiment's stable slug.
        experiment_name: Human-readable name.
        primary_metric: The metric the test was designed to move.
        status: Lifecycle status.
        started_on: First day of the experiment.
        ended_on: Last day, or ``None`` while it is still running.
        traffic_allocation: Fraction of eligible users enrolled.
        variant: Arm label.
        is_control: Whether this arm is the baseline. Read the control arm from this
            column, never from row position.
        n: Users enrolled in the arm.
        successes: Converting users in the arm.
        rate_pct: ``successes / n`` as a percentage, rounded by SQL to three decimals.
            The tested endpoint recomputes rates from the integers instead of using
            this: feeding a rounded numerator into a variance term shifts the
            z-statistic, which is enough to move a borderline result across the
            threshold.
    """

    experiment_key: str
    experiment_name: str
    primary_metric: str
    status: str
    started_on: date | None = None
    ended_on: date | None = None
    traffic_allocation: Number | None = None
    variant: str
    is_control: bool = Field(
        description="Identify the control arm by this column, not by row position.",
    )
    n: int
    successes: int
    rate_pct: Number | None = Field(
        default=None,
        description="Rounded. The tested endpoint recomputes rates from the raw counts.",
    )


class IntervalSchema(PrismModel):
    """A confidence interval on a proportion, in percentage points.

    Attributes:
        low: Lower bound, clamped to 0.
        high: Upper bound, clamped to 100.
        confidence: The level, e.g. ``0.95``.
        width: ``high - low``. A useful proxy for precision — a 40-point-wide interval
            on a conversion rate means the experiment has not yet measured anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    low: float
    high: float
    confidence: float
    width: float = Field(description="Interval width. A precision proxy, computed, not stored.")

    @classmethod
    def from_interval(cls, interval: Interval) -> IntervalSchema:
        """Build from the service-layer dataclass.

        Args:
            interval: The computed interval.

        Returns:
            The response model.
        """
        return cls.model_validate(interval)


class ProportionTestSchema(PrismModel):
    """One variant tested against control.

    Attributes:
        control_n: Users in control.
        control_successes: Converting users in control.
        control_rate_pct: Control conversion rate, recomputed from the counts.
        control_interval: Wilson interval on the control rate.
        variant: Variant label.
        variant_n: Users in the variant.
        variant_successes: Converting users in the variant.
        variant_rate_pct: Variant conversion rate.
        variant_interval: Wilson interval on the variant rate.
        absolute_lift_pp: Variant rate minus control rate, in percentage points.
        relative_lift_pct: The same difference as a share of the control rate, or
            ``None`` when control converted nobody — undefined, not infinite.
        z_statistic: Pooled two-proportion z, or ``None`` when the pooled variance is
            zero. That happens when both arms sit at exactly 0% or exactly 100%: there
            is no evidence of a difference, and also no test to run.
        p_value: Two-sided p-value, floored rather than allowed to reach zero. ``1.0``
            when no statistic could be computed.
        alpha: The significance level used.
        is_significant: Whether ``p_value < alpha`` *and* both arms met the minimum
            size.
        verdict: The conclusion — winner, loser, inconclusive or underpowered. Two
            sided, so a variant that lost significantly is reported as a loser rather
            than folded in with the inconclusive ones.
        observed_power: Post-hoc power at the observed effect. Reported to separate "no
            effect" from "not enough data", and deliberately not used to decide
            significance: post-hoc power is a transform of the p-value, so testing
            against it would be circular.
        intervals_overlap: Whether the two arms' intervals overlap. Non-overlapping
            intervals imply significance, but overlapping ones do not imply its
            absence — the test is the authority, and this is for rendering.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    control_n: int
    control_successes: int
    control_rate_pct: float
    control_interval: IntervalSchema
    variant: str
    variant_n: int
    variant_successes: int
    variant_rate_pct: float
    variant_interval: IntervalSchema
    absolute_lift_pp: float
    relative_lift_pct: float | None = None
    z_statistic: float | None = Field(
        default=None,
        description="Null when pooled variance is zero — both arms at 0% or both at 100%.",
    )
    p_value: float = Field(description="Floored, never exactly zero.")
    alpha: float
    is_significant: bool
    verdict: Verdict
    observed_power: float = Field(
        description="Post-hoc. Reported for context, never used to decide significance.",
    )
    intervals_overlap: bool = Field(
        description="For rendering. Overlap does not disprove significance.",
    )

    @classmethod
    def from_test(cls, test: ProportionTest) -> ProportionTestSchema:
        """Build from the service-layer dataclass.

        Args:
            test: The computed test.

        Returns:
            The response model, including the computed ``intervals_overlap``.
        """
        return cls.model_validate(test)


class ExperimentResultsSchema(PrismModel):
    """One experiment's definition, its control arm, and a test per variant.

    Attributes:
        experiment_key: The experiment's stable slug.
        experiment_name: Human-readable name.
        primary_metric: The metric the test was designed to move.
        status: Lifecycle status.
        started_on: First day of the experiment.
        ended_on: Last day, or ``None`` while it is still running.
        traffic_allocation: Fraction of eligible users enrolled.
        observation_end: The cut-off applied when counting outcomes. ``None`` means
            every outcome on record was counted.
        is_segmented: ``True`` when filters were applied. Filtering re-segments the
            population and invalidates the original randomisation, so a segmented
            result is exploratory and not the experiment's outcome. The flag exists so
            a chart can say so rather than leaving a reader to infer it.
        control_variant: Label of the control arm.
        control_n: Users in control.
        control_successes: Converting users in control.
        variants: One test per non-control variant. Empty when the experiment has a
            control arm and nothing else, which is valid if uninformative.
        alpha: The significance level the verdicts used.
        comparisons: How many variant-versus-control tests were run.
        bonferroni_alpha: The per-test level that would hold the family-wise error rate
            at ``alpha``. Reported, not applied — the verdicts above use ``alpha``
            unadjusted. Applying it silently would change published verdicts on the
            caller's behalf; omitting it would hide a known bias. Both are returned and
            the choice stays with the reader.
        total_n: Users enrolled across every arm, control included.
        has_winner: Whether any variant beat control at ``alpha``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    experiment_key: str
    experiment_name: str
    primary_metric: str
    status: str
    started_on: date | None = None
    ended_on: date | None = None
    traffic_allocation: Number | None = None
    observation_end: date | None = None
    is_segmented: bool = Field(
        description="True means these figures describe a segment, not the experiment's outcome.",
    )
    control_variant: str
    control_n: int
    control_successes: int
    variants: list[ProportionTestSchema] = Field(default_factory=list)
    alpha: float
    comparisons: int
    bonferroni_alpha: float = Field(
        description="Reported, not applied. The verdicts above use the unadjusted alpha.",
    )
    total_n: int
    has_winner: bool

    @classmethod
    def from_summary(cls, summary: ExperimentSummary) -> ExperimentResultsSchema:
        """Build from the service-layer dataclass.

        Args:
            summary: The computed summary.

        Returns:
            The response model, including the four computed properties.
        """
        return cls.model_validate(summary)


__all__ = [
    "ExperimentResultsSchema",
    "ExperimentSummaryRow",
    "IntervalSchema",
    "ProportionTestSchema",
    "VariantMetricRow",
]
