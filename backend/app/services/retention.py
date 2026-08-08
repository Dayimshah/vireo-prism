"""Retention: three definitions, segment comparisons and resurrection.

Wraps :mod:`app.repositories.retention`. The interesting decision here is not in
the code but in what the code refuses to do: the three retention definitions are
exposed as three separate functions rather than one function with a ``definition``
argument, because they answer different questions and are not interchangeable.

Classic N-day retention asks "was the user active on exactly day N". Rolling asks
"on day N or any day after". Unbounded asks "on any day within the first N". Both
looser definitions read higher than classic, and — this is the part that looks like
a bug and is not — neither is nested inside the other, so neither is uniformly
higher than its counterpart. A single endpoint with a mode flag would invite a
caller to swap one for another and compare the results as though they were the same
metric on a different day.

``observation_end`` deserves its own note. It caps how far forward activity is
counted, and it defaults to the window's end. Leaving it at the default means the
most recent cohorts have not had time to reach day 30, so their retention reads
artificially low — the well-known right-censoring artefact. Callers plotting a
retention curve should pass an ``observation_end`` far enough past ``date_to`` for
every cohort to have matured, or restrict ``date_to`` instead.

Everything here is on the long TTL band. These queries scan
``analytics.mv_user_daily`` across every cohort in the window, and they describe a
closed historical period that cannot change until the next refresh.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import retention as repo
from app.repositories.retention import (
    DEFAULT_MIN_COHORT_SIZE,
    RETENTION_SEGMENTS,
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
NAMESPACE = "retention"


async def get_retention_nday(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return classic N-day retention: active on exactly day N.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date activity is counted. Defaults to ``date_to``,
            which right-censors the youngest cohorts — see the module docstring.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.retention.get_retention_nday`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "nday",
        {**window.as_params(), "observation_end": end, **filter_set.as_params()},
        lambda: repo.get_retention_nday(session, window.date_from, window.date_to, end, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_retention_rolling(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return rolling retention: active on day N *or later*.

    Reads higher than classic retention by construction. It is not comparable with
    :func:`get_retention_unbounded` in either direction.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date activity is counted. Defaults to ``date_to``.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.retention.get_retention_rolling`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "rolling",
        {**window.as_params(), "observation_end": end, **filter_set.as_params()},
        lambda: repo.get_retention_rolling(
            session, window.date_from, window.date_to, end, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_retention_unbounded(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return unbounded retention: active on *any* day within the first N.

    Note that ``retention_pct`` is not monotonic in ``day_n`` here, because
    ``cohort_size`` shrinks as ``day_n`` grows — the eligibility rule requires a
    user to have had the full N days to be counted at all. Two rows with different
    ``cohort_size`` describe different populations, so compare within a
    ``cohort_size`` group rather than across the whole series.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date activity is counted. Defaults to ``date_to``.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.retention.get_retention_unbounded`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "unbounded",
        {**window.as_params(), "observation_end": end, **filter_set.as_params()},
        lambda: repo.get_retention_unbounded(
            session, window.date_from, window.date_to, end, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_retention_by_segment(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    segment_by: str,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return retention split by one caller-chosen dimension.

    ``segment_by`` is validated by the repository against
    :data:`app.repositories.retention.RETENTION_SEGMENTS` and reaches SQL as a bound
    parameter that selects between fixed ``CASE`` arms, never as an interpolated
    column name. This service does not re-validate it: two checks against one
    allowlist is one check too many, and the repository's is closest to the SQL that
    depends on it.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        segment_by: Dimension to split by. One of
            :data:`app.repositories.retention.RETENTION_SEGMENTS` — note this
            allowlist differs from the funnel module's.
        observation_end: Last date activity is counted. Defaults to ``date_to``.
        min_cohort_size: Segments smaller than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.retention.get_retention_by_segment`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid, or ``segment_by`` is not in the
            allowlist.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "by_segment",
        {
            **window.as_params(),
            "segment_by": segment_by,
            "observation_end": end,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_retention_by_segment(
            session,
            window.date_from,
            window.date_to,
            segment_by,
            end,
            min_cohort_size,
            filter_set,
        ),
        ttl=Ttl.HEAVY,
    )


async def get_retention_curve_by_persona(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    observation_end: date | None = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return a full retention curve per persona.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: Earliest signup date included, inclusive.
        date_to: Latest signup date included, inclusive.
        observation_end: Last date activity is counted. Defaults to ``date_to``.
        min_cohort_size: Personas with fewer users than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from
        :func:`app.repositories.retention.get_retention_curve_by_persona`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)
    end = observation_end or window.date_to

    return await cached_rows(
        NAMESPACE,
        "curve_by_persona",
        {
            **window.as_params(),
            "observation_end": end,
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_retention_curve_by_persona(
            session, window.date_from, window.date_to, end, min_cohort_size, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_resurrection_rate(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the rate at which dormant users come back.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.retention.get_resurrection_rate`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "resurrection",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_resurrection_rate(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "NAMESPACE",
    "RETENTION_SEGMENTS",
    "get_resurrection_rate",
    "get_retention_by_segment",
    "get_retention_curve_by_persona",
    "get_retention_nday",
    "get_retention_rolling",
    "get_retention_unbounded",
]
