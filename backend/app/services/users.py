"""User segmentation: RFM deciles rolled up into named behavioural segments.

Wraps :mod:`app.repositories.users`. One function, and the only one in the package
that takes neither a date range nor any other argument beyond the filters. RFM
describes the *current* state of the user base, scored against the dataset's latest
activity date, so a window parameter would imply a time-slicing this metric does not
support.

Two properties of the output are easy to misread. The deciles are computed across the
population, so they are relative rankings rather than absolute thresholds — a
"champion" is in the top band *of this dataset*. And because the deciles are computed
over the *filtered* population, a filtered result re-ranks within that subset instead
of reporting where those users sit in the whole base; the segment mix of one country
is not that country's contribution to the global mix.

On the long TTL band: computing deciles requires ranking every user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import users as repo
from app.services.base import FilterRequest, Ttl, cached_rows, resolve_filters

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.deps import DimensionCatalog

#: Cache namespace for this module.
NAMESPACE = "users"


async def get_rfm_segments(
    session: AsyncSession,
    catalog: DimensionCatalog,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the user base rolled up into named RFM segments.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        filters: Optional filters as caller-supplied strings. Note these change the
            population the deciles are computed over — see the module docstring.

    Returns:
        The rows from :func:`app.repositories.users.get_rfm_segments`, unchanged.
        Read ``pct_of_users`` against ``pct_of_revenue``: a segment holding a small
        share of users and a large share of revenue is where retention spending
        belongs.

    Raises:
        UnknownDimensionValueError: If a filter value is unknown.
    """
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "rfm_segments",
        filter_set.as_params(),
        lambda: repo.get_rfm_segments(session, filter_set),
        ttl=Ttl.HEAVY,
    )


__all__ = ["NAMESPACE", "get_rfm_segments"]
