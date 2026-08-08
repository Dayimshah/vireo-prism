"""Geography and hardware: the country league table and the device mix.

Wraps :mod:`app.repositories.geo`.

The two functions take different time arguments, and the asymmetry is intentional.
:func:`get_country_ranking` takes a cut-off date only: it is a lifetime-to-date
ranking, so every user and every dollar up to ``date_to`` counts, and a start date
would silently turn a league table into a windowed one. :func:`get_device_breakdown`
takes a window, because a device mix is a property of a period — it shifts as
hardware ages, and that shift is the interesting part.

Both sit on the long TTL band. The ranking aggregates the whole revenue history per
country; the breakdown groups the session table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import geo as repo
from app.repositories.geo import DEFAULT_MIN_COHORT_SIZE
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
NAMESPACE = "geo"


async def get_country_ranking(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return countries ranked by revenue, with penetration and engagement.

    Takes a cut-off, not a range — see the module docstring.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_to: Last day counted, inclusive. Everything up to and including this
            date contributes.
        min_cohort_size: Countries with fewer users than this are omitted, so a
            country with four users cannot top an ARPU ranking.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.geo.get_country_ranking`, unchanged.

    Raises:
        UnknownDimensionValueError: If a filter value is unknown. Country filters
            accept either the ISO code or the country name.
    """
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "country_ranking",
        {
            "date_to": date_to,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_country_ranking(session, date_to, min_cohort_size, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_device_breakdown(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the device and platform mix over a window.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.geo.get_device_breakdown`, unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "device_breakdown",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_device_breakdown(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "NAMESPACE",
    "get_country_ranking",
    "get_device_breakdown",
]
