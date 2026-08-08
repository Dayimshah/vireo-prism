"""Acquisition economics: attribution, LTV:CAC and payback period.

Wraps :mod:`app.repositories.marketing`. Three views of one question — which
channels deserve more money — and they are kept separate because they disagree
usefully. Attribution describes volume and quality, LTV:CAC judges efficiency, and
payback answers *when* the cash returns. Two channels with identical LTV:CAC are
not equally attractive if one repays in three months and the other in eighteen.

Undefined values arrive as ``None`` throughout: organic channels have zero CAC, so
every ratio derived from it is undefined rather than infinite, and a channel that
has not yet recovered its spend has ``payback_months`` of ``None`` with
``payback_band`` distinguishing that from "no acquisition cost". Neither should be
plotted as zero.

All three group across every channel and every signup in the window, so all three
are on the long TTL band.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import marketing as repo
from app.repositories.marketing import DEFAULT_MIN_COHORT_SIZE
from app.services.base import (
    FilterRequest,
    Ttl,
    cached_rows,
    resolve_filters,
    resolve_window,
)

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.deps import DimensionCatalog

#: Cache namespace for this module.
NAMESPACE = "marketing"


async def get_channel_attribution(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return volume, engagement quality and revenue for each acquisition channel.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.marketing.get_channel_attribution`,
        unchanged. ``never_activated`` is the column separating cheap traffic from
        good traffic.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "channel_attribution",
        {
            **window.as_params(),
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_channel_attribution(
            session, window.date_from, window.date_to, min_cohort_size, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_ltv_to_cac(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return LTV against CAC per channel, with a quadrant classification.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.marketing.get_ltv_to_cac`, unchanged.
        ``quadrant`` is relative to the median LTV and CAC across channels, so it
        describes this portfolio rather than an absolute benchmark.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "ltv_to_cac",
        {
            **window.as_params(),
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_ltv_to_cac(
            session, window.date_from, window.date_to, min_cohort_size, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_cac_payback(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return how many months each channel takes to repay its acquisition cost.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date with revenue data. Defaults to ``date_to``.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.marketing.get_cac_payback`, unchanged.
        ``payback_months`` is ``None`` in two distinct cases that ``payback_band``
        separates — no acquisition cost, and not yet recovered.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "cac_payback",
        {
            **window.as_params(),
            "observation_end": end,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_cac_payback(
            session, window.date_from, window.date_to, end, min_cohort_size, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "NAMESPACE",
    "get_cac_payback",
    "get_channel_attribution",
    "get_ltv_to_cac",
]
