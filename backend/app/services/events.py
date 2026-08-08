"""Event mix: how the raw event stream divides by type.

Wraps :mod:`app.repositories.events`. One query, and it is the closest thing in the
package to a look at the source data — every other module reads the event stream
through some aggregate, and this one describes the stream itself. That makes it the
first place to look when a downstream number moves unexpectedly: if the share of
``play_start`` events shifted, the completion-rate queries were always going to move
with it.

On the long TTL band. It groups the largest table in the schema over the whole
window without narrowing to a subset of event types first, which is the point of the
query and also what makes it expensive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories import events as repo
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
NAMESPACE = "events"


async def get_event_distribution(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> list[dict[str, Any]]:
    """Return the volume and share of each event type over a window.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings.

    Returns:
        The rows from :func:`app.repositories.events.get_event_distribution`,
        unchanged.

    Raises:
        ValidationError: If the window is invalid.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    filter_set = resolve_filters(filters, catalog)

    return await cached_rows(
        NAMESPACE,
        "event_distribution",
        {**window.as_params(), **filter_set.as_params()},
        lambda: repo.get_event_distribution(session, window.date_from, window.date_to, filter_set),
        ttl=Ttl.HEAVY,
    )


__all__ = ["NAMESPACE", "get_event_distribution"]
