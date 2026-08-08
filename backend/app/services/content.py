"""Content performance: leaderboards, completion, decay and genre economics.

Wraps :mod:`app.repositories.content`. Three of the six take a ``limit``, and this
is the module where :func:`app.services.base.resolve_limit` earns its place: the
catalogue is large enough that an unbounded ``limit`` would let one request pull
every title, so the value is clamped to ``PRISM_API__MAX_PAGE_SIZE`` before it
reaches SQL.

``min_starts`` is passed through rather than clamped. It is a noise floor, not a
size limit — a completion rate computed over four viewers describes those four
people — and a caller lowering it is asking for a noisier answer, which is their
prerogative. Raising it can only ever return fewer rows.

The three genre and decay queries sit on the long TTL band. They group across the
whole catalogue and the whole window, and none of them can change until the next
materialized-view refresh.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import content as repo
from app.repositories.content import DEFAULT_LIMIT, DEFAULT_MIN_STARTS
from app.services.base import (
    FilterRequest,
    Ttl,
    cached_rows,
    resolve_filters,
    resolve_limit,
    resolve_window,
)

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.deps import DimensionCatalog

#: Cache namespace for this module.
NAMESPACE = "content"


async def get_top_watch_time(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    limit: int | None = None,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the content leaderboard ranked by total watch time.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        limit: Titles to return. Defaults to
            :data:`app.repositories.content.DEFAULT_LIMIT`; clamped to the
            configured maximum page size.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.content.get_top_watch_time`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid or ``limit`` is below 1.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    rows = resolve_limit(limit, DEFAULT_LIMIT)

    return await cached_rows(
        NAMESPACE,
        "top_watch_time",
        {**window.as_params(), "limit": rows, **filter_set.as_params()},
        lambda: repo.get_top_watch_time(
            session, window.date_from, window.date_to, rows, filter_set
        ),
    )


async def get_completion_rate(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    limit: int | None = None,
    min_starts: int = DEFAULT_MIN_STARTS,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return completion rate per title, with the average abandonment point.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        limit: Titles to return, clamped to the configured maximum.
        min_starts: Titles with fewer starts than this are omitted, so a rate is
            never reported over a handful of viewers.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.content.get_completion_rate`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid or ``limit`` is below 1.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    rows = resolve_limit(limit, DEFAULT_LIMIT)

    return await cached_rows(
        NAMESPACE,
        "completion_rate",
        {
            **window.as_params(),
            "limit": rows,
            "min_starts": min_starts,
            **filter_set.as_params(),
        },
        lambda: repo.get_completion_rate(
            session, window.date_from, window.date_to, rows, min_starts, filter_set
        ),
    )


async def get_trailer_to_start_cvr(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    limit: int | None = None,
    min_starts: int = DEFAULT_MIN_STARTS,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return trailer-to-start conversion per title, against the no-trailer rate.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        limit: Titles to return, clamped to the configured maximum.
        min_starts: Titles with fewer starts than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.content.get_trailer_to_start_cvr`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid or ``limit`` is below 1.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    rows = resolve_limit(limit, DEFAULT_LIMIT)

    return await cached_rows(
        NAMESPACE,
        "trailer_to_start_cvr",
        {
            **window.as_params(),
            "limit": rows,
            "min_starts": min_starts,
            **filter_set.as_params(),
        },
        lambda: repo.get_trailer_to_start_cvr(
            session, window.date_from, window.date_to, rows, min_starts, filter_set
        ),
    )


async def get_shelf_life_decay(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return how engagement decays in the weeks after a title is added.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.content.get_shelf_life_decay`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "shelf_life_decay",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_shelf_life_decay(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_genre_performance_matrix(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return one row per genre across every commissioning dimension.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from
        :func:`app.repositories.content.get_genre_performance_matrix`, unchanged.
        ``watch_per_title_index`` above 1.0 marks a genre earning more attention
        than its share of the catalogue.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "genre_performance_matrix",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_genre_performance_matrix(
            session, window.date_from, window.date_to, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_genre_affinity_by_persona(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return which personas watch which genres, as affinity lift.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from
        :func:`app.repositories.content.get_genre_affinity_by_persona`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "genre_affinity_by_persona",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_genre_affinity_by_persona(
            session, window.date_from, window.date_to, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MIN_STARTS",
    "NAMESPACE",
    "get_completion_rate",
    "get_genre_affinity_by_persona",
    "get_genre_performance_matrix",
    "get_shelf_life_decay",
    "get_top_watch_time",
    "get_trailer_to_start_cvr",
]
