"""Conversion funnels: two funnels, drop-off, elapsed time and segments.

Wraps :mod:`app.repositories.funnel`. The two funnels are scoped differently on
purpose, and conflating them is the mistake this module is arranged to prevent.
:func:`get_discovery_to_watch` is session-scoped — did *this visit* reach playback —
so it measures the browse experience. :func:`get_signup_to_subscribe` is
user-lifetime-scoped, so someone who browsed on Tuesday and subscribed on Friday
counts as converted. Running the second with session scope would report a
conversion rate several times too low and look like a catastrophe.

``segment_by`` on :func:`get_funnel_by_segment` is validated by the repository
against :data:`app.repositories.funnel.FUNNEL_SEGMENTS`, which is deliberately
*not* the same allowlist as the retention module's: this funnel can split by
``form_factor`` and ``platform``, which retention cannot, and retention can split
by ``device``, which this cannot. Two names, two lists, no shared constant — a
single merged allowlist would accept a segment for one query that the other cannot
serve.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import funnel as repo
from app.repositories.funnel import DEFAULT_MIN_COHORT_SIZE, FUNNEL_SEGMENTS
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
NAMESPACE = "funnel"


async def get_discovery_to_watch(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the session-scoped discovery-to-watch funnel.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.funnel.get_discovery_to_watch`,
        unchanged. Scoped to a single visit — not comparable with the signup funnel.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "discovery_to_watch",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_discovery_to_watch(session, window.date_from, window.date_to, filter_set),
    )


async def get_signup_to_subscribe(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the user-scoped signup-to-subscription funnel.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.funnel.get_signup_to_subscribe`,
        unchanged. Scoped to each user's whole lifetime, so a visit-spanning
        conversion is counted.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "signup_to_subscribe",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_signup_to_subscribe(session, window.date_from, window.date_to, filter_set),
    )


async def get_step_dropoff(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the loss between consecutive funnel steps, ranked two ways.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.funnel.get_step_dropoff`, unchanged.
        ``loss_rank`` and ``rate_rank`` usually disagree, and the disagreement is
        the finding: biggest absolute loss and heaviest proportional leak imply
        different fixes.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "step_dropoff",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_step_dropoff(session, window.date_from, window.date_to, filter_set),
    )


async def get_time_between_steps(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return elapsed time between consecutive funnel steps, as percentiles.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.funnel.get_time_between_steps`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "time_between_steps",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_time_between_steps(session, window.date_from, window.date_to, filter_set),
    )


async def get_funnel_by_segment(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    segment_by: str,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the discovery funnel split by a caller-chosen dimension.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        segment_by: Dimension to split by. One of
            :data:`app.repositories.funnel.FUNNEL_SEGMENTS`, which differs from the
            retention module's allowlist. Bound as a parameter selecting between
            fixed ``CASE`` arms, never interpolated.
        min_cohort_size: Segments smaller than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.funnel.get_funnel_by_segment`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid, or ``segment_by`` is not in the
            allowlist.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "by_segment",
        {
            **window.as_params(),
            "segment_by": segment_by,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_funnel_by_segment(
            session,
            window.date_from,
            window.date_to,
            segment_by,
            min_cohort_size,
            filter_set,
        ),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "FUNNEL_SEGMENTS",
    "NAMESPACE",
    "get_discovery_to_watch",
    "get_funnel_by_segment",
    "get_signup_to_subscribe",
    "get_step_dropoff",
    "get_time_between_steps",
]
