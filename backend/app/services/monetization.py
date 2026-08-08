"""Revenue: ARPU trend, MRR movement, and the two conversion views.

Wraps :mod:`app.repositories.monetization`.

:func:`get_arpu_trend` returns ARPU and ARPPU side by side, and the pair is the
point. ARPU divides by all active users, ARPPU only by payers, so
``paying_share_pct`` is the bridge between them: when ARPU moves, the two other
columns say whether pricing changed or the mix did. Reporting one without the other
makes that ambiguous.

:func:`get_conversion_by_watch_decile` is the query that recovers the simulation's
planted signal. Conversion rises monotonically across watch-time deciles because
the seeder drives conversion from watch behaviour through a logistic model, and this
query reads only the event stream — it never sees the coefficients. That is the
project's strongest evidence the data is causal rather than random noise wearing a
schema.

``mrr_movement_waterfall`` measured around two seconds on the small profile, so it
sits on the long TTL band along with the decile query. The other two are cheap
enough for the default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import monetization as repo
from app.repositories.monetization import DEFAULT_MIN_COHORT_SIZE
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
NAMESPACE = "monetization"


async def get_arpu_trend(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return monthly ARPU and ARPPU with the paying share.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.monetization.get_arpu_trend`,
        unchanged. Read ARPU, ARPPU and ``paying_share_pct`` together — see the
        module docstring.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "arpu_trend",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_arpu_trend(session, window.date_from, window.date_to, filter_set),
    )


async def get_mrr_movement(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the MRR movement waterfall: new, expansion, contraction, churn.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.monetization.get_mrr_movement`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "mrr_movement",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_mrr_movement(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_conversion_by_watch_decile(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return subscription conversion by watch-time decile.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from
        :func:`app.repositories.monetization.get_conversion_by_watch_decile`,
        unchanged. Conversion should rise monotonically across deciles; that
        ordering is recovered from the event stream, not asserted by it.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "conversion_by_watch_decile",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_conversion_by_watch_decile(
            session, window.date_from, window.date_to, filter_set
        ),
        ttl=Ttl.HEAVY,
    )


async def get_trial_conversion_by_plan(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return trial-to-paid conversion per subscription plan.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        min_cohort_size: Plans with fewer trials than this are omitted.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from
        :func:`app.repositories.monetization.get_trial_conversion_by_plan`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "trial_conversion_by_plan",
        {
            **window.as_params(),
            "min_cohort_size": min_cohort_size,
            **filter_set.as_params(),
        },
        lambda: repo.get_trial_conversion_by_plan(
            session, window.date_from, window.date_to, min_cohort_size, filter_set
        ),
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "NAMESPACE",
    "get_arpu_trend",
    "get_conversion_by_watch_decile",
    "get_mrr_movement",
    "get_trial_conversion_by_plan",
]
