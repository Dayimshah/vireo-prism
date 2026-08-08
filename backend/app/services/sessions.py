"""Session shape: duration, depth, composition, timing and device switching.

Wraps :mod:`app.repositories.sessions`. Six functions, all taking the same window
and filters, differing only in TTL band: the two that group over every session in
the window — the entry/exit matrix and the hour-by-weekday heatmap — get the long
band, and the four narrower distributions get the default.

One caller-facing hazard is worth repeating here rather than leaving in the
repository. :func:`get_session_duration_percentiles` returns per-form-factor rows
*and* an overall row in one result set, and the SQL orders by ``dimension_type``
ascending — which puts ``'form_factor'`` before ``'overall'``. Select the headline
row by filtering on ``dimension_type == 'overall'``, never by index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import sessions as repo
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
NAMESPACE = "sessions"


async def get_session_duration_percentiles(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return session duration percentiles, overall and by device form factor.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from
        :func:`app.repositories.sessions.get_session_duration_percentiles`,
        unchanged. Form-factor rows arrive *before* the overall row; select the
        headline by ``dimension_type``, not by position.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "duration_percentiles",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_session_duration_percentiles(
            session, window.date_from, window.date_to, filter_set
        ),
    )


async def get_session_depth(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the distribution of how far into a session users get.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.sessions.get_session_depth`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "depth",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_session_depth(session, window.date_from, window.date_to, filter_set),
    )


async def get_events_per_session(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the bucketed distribution of events per session.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.sessions.get_events_per_session`,
        unchanged. Sort on ``bucket_order``, not on ``bucket``.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "events_per_session",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_events_per_session(session, window.date_from, window.date_to, filter_set),
    )


async def get_entry_exit_screens(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return where sessions begin and end, as entry/exit screen pairs.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.sessions.get_entry_exit_screens`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "entry_exit",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_entry_exit_screens(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_device_switching(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return cross-device behaviour: switches between surfaces, and breadth.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.sessions.get_device_switching`,
        unchanged. ``pct_within_type`` is normalised inside each ``row_type``, so
        the two blocks are never summed together.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "device_switching",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_device_switching(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_activity_heatmap(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return session volume by day of week and hour of day.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.sessions.get_activity_heatmap`,
        unchanged. Prefer ``hour_local`` for "when do people watch" charts;
        ``hour_utc`` smears the evening peak across twenty time zones.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "activity_heatmap",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_activity_heatmap(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "NAMESPACE",
    "get_activity_heatmap",
    "get_device_switching",
    "get_entry_exit_screens",
    "get_events_per_session",
    "get_session_depth",
    "get_session_duration_percentiles",
]
