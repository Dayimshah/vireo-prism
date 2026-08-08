"""Cohort analysis: retention matrices, cumulative revenue and LTV by channel.

Wraps :mod:`app.repositories.cohort`. All four functions here are on the long TTL
band without exception — a cohort matrix groups every signup in the window against
every subsequent month of activity, which is the most expensive shape in the query
set, and its answer describes a closed historical period.

Right-censoring is the thing to understand before reading any output from this
module. A cohort that signed up last month cannot have a month-6 retention figure,
so those cells come back ``None`` rather than zero — ``None`` means "not yet
observable", and plotting it as zero would draw a cliff that does not exist.
``observation_end`` controls where that boundary falls; it defaults to the window's
end.

``max_months`` and ``max_weeks`` cap the matrix width. They are passed through
rather than clamped: unlike a row ``limit``, a wider matrix does not scan more data,
it only pivots more columns from the same scan, and the query already bounds itself
by what the window can support.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import cohort as repo
from app.repositories.cohort import (
    DEFAULT_MAX_MONTHS,
    DEFAULT_MAX_WEEKS,
    DEFAULT_MIN_COHORT_SIZE,
)
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
NAMESPACE = "cohort"


async def get_monthly_matrix(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    max_months: int = DEFAULT_MAX_MONTHS,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the monthly cohort retention matrix.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup month included, inclusive.
        date_to: Latest signup month included, inclusive.
        observation_end: Last date activity is counted. Defaults to ``date_to``.
        max_months: Widest month offset returned.
        min_cohort_size: Cohorts smaller than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.cohort.get_monthly_matrix`, unchanged.
        Unobservable cells are ``None``, never zero.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "monthly_matrix",
        {
            **window.as_params(),
            "observation_end": end,
            "max_months": max_months,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_monthly_matrix(
            session,
            window.date_from,
            window.date_to,
            end,
            max_months,
            min_cohort_size,
            filter_set,
        ),
        ttl=Ttl.HEAVY,
    )


async def get_weekly_matrix(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    max_weeks: int = DEFAULT_MAX_WEEKS,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the weekly signup cohort retention matrix.

    Weekly grain resolves onboarding changes that a monthly matrix averages away,
    at the cost of smaller and noisier cohorts.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup week included, inclusive.
        date_to: Latest signup week included, inclusive.
        observation_end: Last date activity is counted. Defaults to ``date_to``.
        max_weeks: Widest week offset returned.
        min_cohort_size: Cohorts smaller than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.cohort.get_weekly_matrix`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "weekly_matrix",
        {
            **window.as_params(),
            "observation_end": end,
            "max_weeks": max_weeks,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_weekly_matrix(
            session,
            window.date_from,
            window.date_to,
            end,
            max_weeks,
            min_cohort_size,
            filter_set,
        ),
        ttl=Ttl.HEAVY,
    )


async def get_revenue_cumulative(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return cumulative revenue per signup cohort, by month since signup.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup month included, inclusive.
        date_to: Latest signup month included, inclusive.
        observation_end: Last date revenue is counted. Defaults to ``date_to``.
        min_cohort_size: Cohorts smaller than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.cohort.get_revenue_cumulative`,
        unchanged. Compare cohorts on ``cumulative_arpu_usd``, not on absolute
        revenue — cohort sizes differ.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "revenue_cumulative",
        {
            **window.as_params(),
            "observation_end": end,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_revenue_cumulative(
            session, window.date_from, window.date_to, end, min_cohort_size, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_ltv_by_channel(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return lifetime value per acquisition channel, against CAC.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date revenue is counted. Defaults to ``date_to``.
        min_cohort_size: Channels with fewer acquisitions than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.cohort.get_ltv_by_channel`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "ltv_by_channel",
        {
            **window.as_params(),
            "observation_end": end,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_ltv_by_channel(
            session, window.date_from, window.date_to, end, min_cohort_size, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "DEFAULT_MAX_MONTHS",
    "DEFAULT_MAX_WEEKS",
    "DEFAULT_MIN_COHORT_SIZE",
    "NAMESPACE",
    "get_ltv_by_channel",
    "get_monthly_matrix",
    "get_revenue_cumulative",
    "get_weekly_matrix",
]
