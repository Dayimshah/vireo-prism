"""Churn: the monthly reason mix, and a per-user risk scorecard.

Wraps :mod:`app.repositories.churn`. The two functions have deliberately different
shapes, and the difference is not an oversight.

:func:`get_reason_mix` takes a window: cancellations happen on dates, so the mix
over a period is a meaningful question. :func:`get_risk_scorecard` takes no dates at
all — a risk score describes the present state of each subscriber, anchored to the
dataset's latest activity date so the result is reproducible rather than dependent
on when it was run. Asking for "churn risk in March" would be asking for a
prediction made in March, which this data cannot support.

``min_risk_score`` is a floor on which users appear, not a noise threshold: lowering
it returns more users, and the score itself does not change. ``limit`` is clamped,
because the scorecard is one row per subscriber and the table is large.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import churn as repo
from app.repositories.churn import DEFAULT_LIMIT, DEFAULT_MIN_RISK_SCORE
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
NAMESPACE = "churn"


async def get_reason_mix(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the monthly mix of cancellation reasons, with revenue lost.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.churn.get_reason_mix`, unchanged.
        ``early_churn_pct`` is the column worth watching — it points at onboarding
        rather than at the product.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "reason_mix",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_reason_mix(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


async def get_risk_scorecard(
    session: AsyncSession,
    catalog: DimensionCatalog,
    limit: int | None = None,
    min_risk_score: int = DEFAULT_MIN_RISK_SCORE,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return current subscribers ranked by churn risk, with score components.

    Takes no date range by design — see the module docstring.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        limit: Subscribers to return. Defaults to
            :data:`app.repositories.churn.DEFAULT_LIMIT`; clamped to the configured
            maximum page size.
        min_risk_score: Only subscribers scoring at least this are returned.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.churn.get_risk_scorecard`, unchanged.
        The component columns are returned alongside the total so a reader can see
        *why* a user scored highly, rather than being handed an opaque number.

    Raises:
        ValidationError: If ``limit`` is below 1.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    filter_set = resolve_filters(filters, catalog)
    rows = resolve_limit(limit, DEFAULT_LIMIT)

    return await cached_rows(
        NAMESPACE,
        "risk_scorecard",
        {
            "limit": rows,
            "min_risk_score": min_risk_score,
            **filter_set.as_params(),
        },
        lambda: repo.get_risk_scorecard(session, rows, min_risk_score, filter_set),
        ttl=Ttl.HEAVY,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MIN_RISK_SCORE",
    "NAMESPACE",
    "get_reason_mix",
    "get_risk_scorecard",
]
