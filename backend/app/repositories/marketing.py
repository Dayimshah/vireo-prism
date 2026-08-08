"""Acquisition economics: attribution, LTV:CAC and payback period by channel.

Three views of the same question — which channels are worth more money — from
three angles that disagree usefully.

:func:`get_channel_attribution` is the descriptive one: volume, engagement quality
and revenue per channel, including ``never_activated``, the share of acquisitions
that signed up and never came back. A cheap channel delivering users who never
activate is not cheap.

:func:`get_ltv_to_cac` is the judgement one. It classifies each channel into a
``quadrant`` against the median LTV and median CAC across channels — "scale up",
"efficient but expensive", "cheap but weak", "cut or fix" — so the output is a
recommendation rather than a table to interpret. Medians rather than means as the
split, because one outlier channel would otherwise move the boundary for every
other.

:func:`get_cac_payback` is the cash-flow one, and it answers what the ratio cannot:
*when* the money comes back. Two channels with identical LTV:CAC are not equally
attractive if one repays in three months and the other in eighteen.

Undefined is ``None``, not zero or infinity
-------------------------------------------
Organic channels have zero CAC, so every ratio derived from it is undefined. All
three queries return ``None`` there rather than a sentinel: a large number would
be plotted as though it were a measurement, and zero would read as failure. The
same applies to ``payback_months`` for a channel that has not yet recovered its
spend — ``None`` with ``payback_band`` reading "not yet recovered", which is a
different statement from "recovers slowly".

CAC is a blended per-user cost carried on the channel dimension, not a spend
figure reconstructed from a marketing platform. Total spend is therefore
``cac_usd * users_acquired``, which is exact here in a way it never is in
production, and is worth saying rather than implying.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Channels with fewer acquisitions than this are omitted. A channel with nine
#: users produces a conversion rate and an LTV that describe those nine people.
DEFAULT_MIN_COHORT_SIZE: Final[int] = 30


async def get_channel_attribution(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return volume, engagement quality and revenue for each acquisition channel.

    ``never_activated`` is the column that separates cheap traffic from good
    traffic: users who signed up through the channel and never returned.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per channel with keys ``channel``, ``channel_group``, ``is_paid``,
        ``cac_usd``, ``users_acquired``, ``never_activated``,
        ``never_activated_pct``, ``avg_sessions``, ``avg_watch_hours``,
        ``avg_completion_rate``, ``avg_titles_watched``, ``converted_users``,
        ``conversion_pct``, ``churned_users``, ``churn_pct``,
        ``total_revenue_usd``, ``current_mrr_usd``, ``total_spend_usd``,
        ``net_contribution_usd``, ``share_of_users_pct`` and
        ``share_of_revenue_pct``.
    """
    return await fetch_all(
        session,
        "marketing/channel_attribution_summary",
        {
            "date_from": date_from,
            "date_to": date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_ltv_to_cac(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return LTV against CAC per channel, with a quadrant classification.

    ``quadrant`` places each channel against the *median* LTV and CAC across
    channels, so the label is relative to this portfolio rather than to an absolute
    benchmark. Both the mean and median LTV are returned; a wide gap between them
    indicates the channel's value is concentrated in a few users, which makes the
    mean a poor basis for a spend decision.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per channel, ordered by LTV:CAC descending, with keys ``channel``,
        ``channel_group``, ``is_paid``, ``users_acquired``, ``converted``,
        ``conversion_pct``, ``cac_usd``, ``ltv_per_user_usd``,
        ``total_revenue_usd``, ``total_spend_usd``, ``ltv_to_cac_ratio``,
        ``avg_watch_hours``, ``avg_completion_rate``, ``median_ltv_usd``,
        ``median_cac_usd``, ``quadrant`` and ``is_profitable``.

        ``ltv_to_cac_ratio`` is ``None`` for zero-CAC channels, which are labelled
        ``quadrant='organic'`` rather than being ranked against paid channels.
    """
    return await fetch_all(
        session,
        "marketing/ltv_to_cac_ratio",
        {
            "date_from": date_from,
            "date_to": date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_cac_payback(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return how many months each channel takes to repay its acquisition cost.

    The cash-flow view that LTV:CAC omits. ``payback_months`` is the first month in
    which a channel's cumulative revenue exceeded its total spend.

    Args:
        session: A read-only session.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date with revenue data. Defaults to ``date_to``.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per channel, paid channels first then by payback speed, with keys
        ``channel``, ``channel_group``, ``is_paid``, ``users_acquired``,
        ``cac_per_user_usd``, ``total_spend_usd``, ``revenue_to_date_usd``,
        ``net_position_usd``, ``payback_months``, ``payback_band``,
        ``revenue_per_user_usd`` and ``ltv_to_cac_ratio``.

        ``payback_months`` is ``None`` in two distinct cases, which
        ``payback_band`` separates: a zero-CAC channel ("no acquisition cost") and a
        channel whose spend has not yet been recovered within the observed window
        ("not yet recovered"). The second is a real finding, not missing data.
    """
    return await fetch_all(
        session,
        "marketing/cac_payback_period",
        {
            "date_from": date_from,
            "date_to": date_to,
            "observation_end": observation_end or date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "get_cac_payback",
    "get_channel_attribution",
    "get_ltv_to_cac",
]
