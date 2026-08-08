"""A/B test results: variant counts from SQL, significance from :mod:`.stats`.

Wraps :mod:`app.repositories.experiments`, and it is the only service in the package
that *computes* rather than passes through. The SQL returns ``n`` and ``successes``
per variant and deliberately stops there; this module turns each variant into a
:class:`~app.services.stats.ProportionTest` against control.

Statistics are computed after the cache, not before
---------------------------------------------------
Only the repository rows are cached. The tests are recomputed on every call, for two
reasons. A :class:`~app.services.stats.ProportionTest` is a dataclass and the cache
codec handles scalars, so caching one would mean either a bespoke encoder or storing
something that comes back as an untyped dict. And the arithmetic is a handful of
microseconds against a query measured in hundreds of milliseconds, so there is
nothing to gain.

That is also why ``alpha`` and ``min_arm_size`` are absent from the cache key. They
change the verdict but not the counts, and including them would fragment the cache
for no benefit. This is safe precisely because neither ever reaches SQL — if that
ever changed, the key would have to change with it.

Counts, not the rate column
---------------------------
``rate_pct`` comes back from SQL and is discarded. It is rounded to three decimals,
and feeding a rounded numerator into a variance term shifts the z-statistic — enough
to move a borderline result across the threshold. The rates reported here are
recomputed from the integers by :mod:`app.services.stats`.

Multiple comparisons
--------------------
Verdicts use the ``alpha`` given, unadjusted, and
:attr:`ExperimentSummary.bonferroni_alpha` reports what it would be if it were
corrected for the number of variants tested. Three variants against one control at
alpha=0.05 carry roughly a 14% chance of at least one false positive, so the
correction matters — but applying it silently would change published verdicts on the
caller's behalf, and dropping it silently would hide a known bias. Both numbers are
returned and the choice stays with the reader.

The control arm is read from ``is_control``, never from row position. The SQL orders
control first, so the two agree, but a column that exists to identify the baseline is
the thing to trust.
"""

from __future__ import annotations

from dataclasses import dataclass

# Imported at runtime, not under TYPE_CHECKING, so `ExperimentSummary`'s annotations
# stay resolvable by `typing.get_type_hints`. The dataclass itself does not need them
# — with `from __future__ import annotations` it never evaluates a field annotation —
# but anything introspecting the class does, and phase 9's response schema will.
from datetime import date  # noqa: TC003
from decimal import Decimal  # noqa: TC003
from typing import TYPE_CHECKING, Any

from app.core.exceptions import NotFoundError, PrismError, ValidationError
from app.repositories import experiments as repo
from app.services.base import FilterRequest, Ttl, cached_rows, resolve_filters
from app.services.stats import (
    DEFAULT_ALPHA,
    MIN_ARM_SIZE,
    ProportionTest,
    Verdict,
    compare_proportions,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.deps import DimensionCatalog

#: Cache namespace for this module.
NAMESPACE = "experiments"


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    """One experiment's definition, its control arm, and a test per variant.

    Attributes:
        experiment_key: The experiment's stable slug.
        experiment_name: Human-readable name.
        primary_metric: The metric the test was designed to move.
        status: Lifecycle status carried on ``core.experiments``.
        started_on: First day of the experiment.
        ended_on: Last day, or ``None`` while it is still running.
        traffic_allocation: Fraction of eligible users enrolled, in ``(0, 1]``.
        observation_end: The cut-off applied when counting outcomes. ``None`` means
            every outcome on record was counted.
        is_segmented: ``True`` when filters were applied. Filtering re-segments the
            population and invalidates the original randomisation, so a segmented
            result is exploratory and not the experiment's outcome. The flag exists
            so a chart can say so rather than leaving the reader to infer it.
        control_variant: Label of the control arm.
        control_n: Users in control.
        control_successes: Converting users in control.
        variants: One test per non-control variant, in the order SQL returned them.
            Empty when the experiment has a control arm and nothing else, which is a
            valid if uninformative state.
        alpha: The significance level the verdicts used.
    """

    experiment_key: str
    experiment_name: str
    primary_metric: str
    status: str
    started_on: date | None
    ended_on: date | None
    traffic_allocation: Decimal | None
    observation_end: date | None
    is_segmented: bool
    control_variant: str
    control_n: int
    control_successes: int
    variants: list[ProportionTest]
    alpha: float

    @property
    def comparisons(self) -> int:
        """Return how many variant-versus-control tests were run."""
        return len(self.variants)

    @property
    def bonferroni_alpha(self) -> float:
        """Return the per-test level that holds the family-wise error rate at alpha.

        Reported, not applied — see the module docstring. Equal to :attr:`alpha` when
        there is a single variant, where there is nothing to correct for.
        """
        return self.alpha / self.comparisons if self.comparisons else self.alpha

    @property
    def total_n(self) -> int:
        """Return the users enrolled across every arm, control included."""
        return self.control_n + sum(test.variant_n for test in self.variants)

    @property
    def has_winner(self) -> bool:
        """Return whether any variant beat control at :attr:`alpha`."""
        return any(test.verdict is Verdict.WINNER for test in self.variants)


async def list_experiments(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every experiment defined in the dataset, newest first.

    Takes no catalogue and no filters, unlike every other function in this package.
    Both would be meaningless against a table of definitions — see
    :func:`app.repositories.experiments.list_experiments`.

    Args:
        session: A read-only session.

    Returns:
        The rows from :func:`app.repositories.experiments.list_experiments`,
        unchanged. Empty on a migrated but unseeded database, which is a valid state
        rather than a failure.
    """
    return await cached_rows(
        NAMESPACE,
        "catalogue",
        # No parameters, so no key material beyond the namespace and name. Passed as an
        # empty dict rather than omitted, so this shares the key shape every other
        # cached call in the project uses.
        {},
        lambda: repo.list_experiments(session),
        # Definitions change only when someone reseeds, which restarts the app anyway.
        # `HEAVY` would be defensible; the query is four rows and a count, so the
        # lifetime is about how stale a picker may be, not about query cost.
        ttl=Ttl.DEFAULT,
    )


async def get_variant_metrics(
    session: AsyncSession,
    catalog: DimensionCatalog,
    experiment_key: str,
    observation_end: date | None = None,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the raw per-variant counts for one experiment, uninterpreted.

    The input to :func:`get_results`, exposed separately so a caller can see the
    counts a verdict was derived from — a table beside the chart, or a check that a
    surprising result is not a data problem.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        experiment_key: The experiment's stable slug.
        observation_end: Last date an outcome counts. ``None`` counts every outcome
            on record, which is correct for a completed experiment and is why this
            does not default to today.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.experiments.get_variant_metrics`,
        unchanged. An empty list means no experiment matched ``experiment_key``;
        :func:`get_results` turns that into a 404.

    Raises:
        UnknownDimensionValueError: If a filter value is unknown.
    """
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "variant_metrics",
        {
            "experiment_key": experiment_key,
            "observation_end": observation_end,
            **filter_set.as_params(),
        },
        lambda: repo.get_variant_metrics(session, experiment_key, observation_end, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_results(
    session: AsyncSession,
    catalog: DimensionCatalog,
    experiment_key: str,
    observation_end: date | None = None,
    alpha: float = DEFAULT_ALPHA,
    min_arm_size: int = MIN_ARM_SIZE,
    filters: FilterRequest | None = None,
) -> ExperimentSummary:
    """Return one experiment's results with a significance test per variant.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        experiment_key: The experiment's stable slug.
        observation_end: Last date an outcome counts. ``None`` counts everything.
        alpha: Two-sided significance level for every variant test.
        min_arm_size: Below this many users in either arm the variant is reported as
            :attr:`~app.services.stats.Verdict.UNDERPOWERED` rather than tested.
        filters: Optional filters as caller-supplied strings. Applying any sets
            :attr:`ExperimentSummary.is_segmented`.

    Returns:
        The summary. Verdicts are two-sided, so a variant that lost significantly is
        reported as :attr:`~app.services.stats.Verdict.LOSER` rather than folded in
        with the inconclusive ones — one of the seeded experiments is genuinely
        negative, and two have a true lift of exactly zero.

    Raises:
        NotFoundError: If no experiment matches ``experiment_key``.
        ValidationError: If ``alpha`` is not strictly inside ``(0, 1)``, or if the
            filters excluded every enrolled user — see the note below.
        UnknownDimensionValueError: If a filter value is unknown.
        PrismError: If the rows contain no control arm. A 500, because the caller
            did nothing wrong and cannot fix it: an experiment without a baseline is
            a broken definition, and guessing which arm was meant to be the control
            would publish a verdict derived from a guess.

    Note:
        An empty result is ambiguous when filters are applied, and the two causes
        need different answers. The filter is applied inside the query's join, so an
        unknown key and a filter that matched nobody both come back as zero rows —
        and reporting the second as a 404 would tell a caller their experiment does
        not exist when it does, sending them hunting for a typo they did not make.
        Only on that empty path is a second, unfiltered lookup issued to tell the two
        apart. It costs one extra query in the case that already returned nothing.
    """
    rows = await get_variant_metrics(
        session,
        catalog,
        experiment_key,
        observation_end,
        filters,
    )
    if not rows:
        if filters is not None and filters.is_active:
            exists = await get_variant_metrics(
                session, catalog, experiment_key, observation_end, None
            )
            if exists:
                raise ValidationError(
                    f"Experiment {experiment_key!r} exists but no enrolled user matches "
                    "the requested filters, so there is nothing to compare. Loosen the "
                    "filters or request the experiment unfiltered.",
                    errors=[{"field": "filters", "message": "excluded every enrolled user"}],
                )
        raise NotFoundError("experiment", experiment_key)

    control = next((row for row in rows if row["is_control"]), None)
    if control is None:
        raise PrismError(
            f"Experiment {experiment_key!r} returned {len(rows)} variant(s) but no "
            "control arm, so no comparison can be made."
        )

    control_n = int(control["n"])
    control_successes = int(control["successes"])

    variants = [
        compare_proportions(
            control_successes,
            control_n,
            int(row["successes"]),
            int(row["n"]),
            variant=str(row["variant"]),
            alpha=alpha,
            min_arm_size=min_arm_size,
        )
        for row in rows
        if not row["is_control"]
    ]

    return ExperimentSummary(
        experiment_key=str(control["experiment_key"]),
        experiment_name=str(control["experiment_name"]),
        primary_metric=str(control["primary_metric"]),
        status=str(control["status"]),
        started_on=control["started_on"],
        ended_on=control["ended_on"],
        traffic_allocation=control["traffic_allocation"],
        observation_end=observation_end,
        is_segmented=filters is not None and filters.is_active,
        control_variant=str(control["variant"]),
        control_n=control_n,
        control_successes=control_successes,
        variants=variants,
        alpha=alpha,
    )


__all__ = [
    "DEFAULT_ALPHA",
    "MIN_ARM_SIZE",
    "NAMESPACE",
    "ExperimentSummary",
    "get_results",
    "get_variant_metrics",
    "list_experiments",
]
