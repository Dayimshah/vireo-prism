"""Cohort analysis: retention matrices and cumulative revenue by signup cohort.

Four queries that group users by when they arrived and follow each group forward.
Two return retention triangles (monthly and weekly), two follow revenue
(cumulative per cohort, and lifetime value per acquisition channel).

Incomplete cells are ``None``, never zero
-----------------------------------------
This is the contract detail that matters most in this module. A cohort that signed
up last month cannot have a month-6 retention value yet. The queries return
``None`` for those cells and flag them with ``is_complete = False``, rather than
returning ``0``.

The distinction is not cosmetic. Rendering an unobservable cell as zero draws a
retention triangle that appears to collapse to nothing along its diagonal — the
classic cohort-chart artefact that gets read as catastrophic churn when it is
simply the absence of data. Callers must treat ``None`` as "not yet known" and
leave the cell blank. ``is_complete`` is provided so a renderer can decide without
inferring intent from a null.

Cohorts smaller than ``min_cohort_size`` are suppressed entirely: a cohort of nine
produces retention percentages that move eleven points per person, and plotting
that as a trend is worse than omitting the row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Cohorts smaller than this are omitted from every query in this module.
DEFAULT_MIN_COHORT_SIZE: Final[int] = 30

#: Default width of the monthly retention triangle, in months.
DEFAULT_MAX_MONTHS: Final[int] = 12

#: Default width of the weekly retention triangle, in weeks.
DEFAULT_MAX_WEEKS: Final[int] = 12


async def get_monthly_matrix(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    max_months: int = DEFAULT_MAX_MONTHS,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the monthly cohort retention matrix.

    Args:
        session: A read-only session.
        date_from: Earliest signup month included, inclusive.
        date_to: Latest signup month included, inclusive.
        observation_end: Last date with activity data, which determines where the
            triangle's observable edge falls. Defaults to ``date_to``.
        max_months: Number of month columns to return per cohort.
        min_cohort_size: Cohorts smaller than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per cohort and month offset, ordered by cohort then offset, with
        keys ``cohort_month``, ``month_n``, ``cohort_size``, ``is_complete``,
        ``active_users`` and ``retention_pct``.

        Where ``is_complete`` is ``False``, both ``active_users`` and
        ``retention_pct`` are ``None`` — the month has not fully elapsed within the
        observation window. Render those cells as blank, not as zero.
    """
    return await fetch_all(
        session,
        "cohort/cohort_monthly_matrix",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            "max_months": max_months,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_weekly_matrix(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    max_weeks: int = DEFAULT_MAX_WEEKS,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the weekly signup cohort retention matrix.

    The weekly grain resolves onboarding and first-week behaviour that a monthly
    matrix averages away, at the cost of noisier cells.

    Args:
        session: A read-only session.
        date_from: Earliest signup week included, inclusive.
        date_to: Latest signup week included, inclusive.
        observation_end: Last date with activity data. Defaults to ``date_to``.
        max_weeks: Number of week columns to return per cohort.
        min_cohort_size: Cohorts smaller than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per cohort and week offset, ordered by cohort then offset, with keys
        ``cohort_week``, ``week_n``, ``cohort_size``, ``is_complete``,
        ``active_users`` and ``retention_pct``.

        As with the monthly matrix, ``active_users`` and ``retention_pct`` are
        ``None`` wherever ``is_complete`` is ``False``.
    """
    return await fetch_all(
        session,
        "cohort/cohort_signup_weekly_matrix",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            "max_weeks": max_weeks,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_revenue_cumulative(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return cumulative revenue per signup cohort, by month since signup.

    ``cumulative_arpu_usd`` is the per-user figure, and it is the one to compare
    against CAC: it answers "how much has a user from this cohort returned so far",
    which is the question a payback calculation asks.

    Older cohorts extend further along the month axis than younger ones, so the
    result is a triangle rather than a rectangle. Comparing cohorts at the *same*
    ``month_n`` is the valid comparison; comparing their final values is not, since
    those sit at different ages.

    Args:
        session: A read-only session.
        date_from: Earliest signup month included, inclusive.
        date_to: Latest signup month included, inclusive.
        observation_end: Last date with revenue data. Defaults to ``date_to``.
        min_cohort_size: Cohorts smaller than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per cohort and month offset, ordered by cohort then offset, with
        keys ``cohort_month``, ``month_n``, ``cohort_size``, ``revenue_usd``,
        ``cumulative_revenue_usd`` and ``cumulative_arpu_usd``.
    """
    return await fetch_all(
        session,
        "cohort/cohort_revenue_cumulative",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_ltv_by_channel(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return lifetime value per acquisition channel, against CAC.

    The headline marketing finding, and one the simulation was arranged to make
    discoverable rather than to assert. The query reads none of the generator's
    coefficients: revenue is recognised from ``core.subscriptions``, CAC comes from
    the channel dimension, and the ratio falls out of the two.

    Two revenue-per-user figures are returned and they answer different questions.
    ``ltv_per_acquired_usd`` divides by everyone acquired and is the figure
    comparable to CAC. ``revenue_per_payer_usd`` divides by paying users only; it is
    higher by construction, useful for pricing, and misleading for channel
    comparison because it hides how many acquisitions never converted.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date with revenue data. Defaults to ``date_to``.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per channel with keys ``channel``, ``channel_group``, ``is_paid``,
        ``users_acquired``, ``users_converted``, ``conversion_pct``, ``cac_usd``,
        ``total_revenue_usd``, ``ltv_per_acquired_usd``, ``revenue_per_payer_usd``,
        ``ltv_to_cac_ratio``, ``total_spend_usd`` and ``net_contribution_usd``.

        ``ltv_to_cac_ratio`` is ``None`` for organic channels, whose CAC is zero:
        the ratio is undefined rather than infinite, and a sentinel value would end
        up plotted as if it were a measurement.
    """
    return await fetch_all(
        session,
        "cohort/cohort_ltv_by_channel",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_MAX_MONTHS",
    "DEFAULT_MAX_WEEKS",
    "DEFAULT_MIN_COHORT_SIZE",
    "get_ltv_by_channel",
    "get_monthly_matrix",
    "get_revenue_cumulative",
    "get_weekly_matrix",
]
