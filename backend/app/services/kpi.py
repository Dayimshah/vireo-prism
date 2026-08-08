"""Headline engagement KPIs: the dashboard's top row.

Six functions over :mod:`app.repositories.kpi`, each adding the three things the
repository layer deliberately leaves out: filter resolution, window validation and
caching.

The shape every service function in this package follows
--------------------------------------------------------
``(session, catalog, window arguments, filters)`` in, ``list[dict]`` out::

    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    return await cached_rows(namespace, name, params, producer, ttl)

Four properties of that shape are load-bearing rather than stylistic.

*The catalogue is a parameter, not a global.* Every service needs it to translate
``"India"`` into a ``country_id``. It could be fetched from
:func:`app.db.deps.get_dimension_catalog`, but that module imports ``fastapi``, so
a runtime import would pull the web framework into the service layer through the
back door — and from there into any test or script that touches a service. Passing
it in keeps the dependency explicit and the import annotation-only, which is why
:class:`~app.db.deps.DimensionCatalog` appears below under ``TYPE_CHECKING``.

*Validation happens before the cache lookup, not after.* An invalid window must
raise whether or not an answer happens to be cached; ordering it the other way
would make a 422 depend on cache state.

*The cache key carries the resolved parameters, not the requested ones.*
``?country=IN`` and ``?country=India`` resolve to the same ``country_id`` and must
therefore share a cache entry. Keying on the raw strings would compute the same
answer twice and store it twice.

*Row shapes are documented in the repository, not here.* Each function below names
the repository function it wraps rather than restating its twenty column names. One
description of a result shape can be wrong; two will eventually disagree, and the
copy further from the SQL is the one that goes stale.

All six of these are the shortest TTL band. They are the numbers a reader watches
while clicking around, so a stale DAU is more annoying than a recomputed one is
expensive — and each is a narrow scan over ``analytics.mv_user_daily``, which is
what makes that affordable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import kpi as repo
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

#: Cache namespace for this module. Matches the SQL namespace and the module name,
#: so ``POST /admin/refresh`` can invalidate one domain by name.
NAMESPACE = "kpi"


async def get_dau(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return daily active users with session and watch-time context.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue, used to resolve filter values.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.kpi.get_dau`, unchanged.

    Raises:
        ValidationError: If the window runs backwards or exceeds the configured
            maximum.
        UnknownDimensionValueError: If a filter value is not in its dimension
            table.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "dau",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_dau(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.KPI,
    )


async def get_wau(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return weekly active users on a 7-day rolling basis.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.kpi.get_wau`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "wau",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_wau(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.KPI,
    )


async def get_mau(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return monthly active users on a 28-day rolling basis.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.kpi.get_mau`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "mau",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_mau(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.KPI,
    )


async def get_stickiness(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return DAU as a percentage of rolling MAU.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.kpi.get_stickiness`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "stickiness",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_stickiness(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.KPI,
    )


async def get_new_vs_returning(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the daily split of new, returning and resurrected active users.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.kpi.get_new_vs_returning`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "new_vs_returning",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_new_vs_returning(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.KPI,
    )


async def get_sessions_per_user(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return sessions per active user per day, with the distribution around it.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.kpi.get_sessions_per_user`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "sessions_per_user",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_sessions_per_user(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.KPI,
    )


__all__ = [
    "NAMESPACE",
    "get_dau",
    "get_mau",
    "get_new_vs_returning",
    "get_sessions_per_user",
    "get_stickiness",
    "get_wau",
]
