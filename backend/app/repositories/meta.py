"""Dataset metadata: what the data covers, and whether it is there at all.

Two queries, neither of them analytics. They answer the questions a client has to ask
*before* it can request a chart: what date range does this dataset cover, and is it
seeded?

Why the window endpoints need this
----------------------------------
The window parameters in :mod:`app.schemas.params` deliberately have no defaults. A "last
30 days" default would open every chart empty on a repository cloned months after its
data was generated, and an empty chart reads as a broken service rather than as a badly
chosen window. A client reads the bounds first, then picks a window inside them.

Why two queries and not one
---------------------------
``meta/dataset_counts`` reads only ``core`` and the system catalogue.
``meta/activity_bounds`` reads ``analytics.mv_user_daily``.

That split is forced. A materialized view created ``WITH NO DATA`` and never refreshed
raises ``materialized view has not been populated`` on any read, which
:func:`app.repositories.base.fetch_all` translates into a
:class:`~app.core.exceptions.StaleAnalyticsError` — a 503. A single combined query would
therefore fail with a server error in exactly the situation it exists to describe, and
the caller would learn that the service is broken rather than that the database needs
seeding. Splitting them lets the safe half report ``analytics_ready`` so the service can
decide whether reading the other half is even possible.

No filters here
---------------
Unlike every other repository module, nothing here takes a :class:`FilterSet`. These
figures describe the dataset as a whole; a per-country event count would be a different
question, and answering it from an endpoint named "bounds" would invite a reader to
mistake a segment's extent for the dataset's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories.base import fetch_one

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_dataset_counts(session: AsyncSession) -> dict[str, Any]:
    """Return user and event counts, and whether the analytics views are populated.

    Safe on a migrated-but-unseeded database: reads only ``core`` tables and
    ``pg_class``, never a materialized view.

    Args:
        session: A read-only session.

    Returns:
        A mapping with ``users``, ``approx_events`` and ``analytics_ready``.
        ``approx_events`` is the planner's estimate summed across the event table's
        partitions, not an exact count — see the query for why the parent's own estimate
        would report zero.

    Raises:
        QueryNotFoundError: If the query is not registered.
        DatabaseError: On any driver failure.
    """
    # The query aggregates to exactly one row, so `fetch_one` cannot return None here.
    # Falling back to an empty mapping rather than asserting keeps the caller's
    # `.get()`-based reading honest if that ever stops being true.
    return await fetch_one(session, "meta/dataset_counts") or {}


async def get_activity_bounds(session: AsyncSession) -> dict[str, Any]:
    """Return the first and last day the dataset has activity for.

    Reads ``analytics.mv_user_daily``, so this **must not** be called when the analytics
    views are unpopulated — check ``analytics_ready`` from :func:`get_dataset_counts`
    first. :mod:`app.services.meta` does exactly that.

    Args:
        session: A read-only session.

    Returns:
        A mapping with ``first_activity_date``, ``last_activity_date`` and ``days``. All
        three are ``None`` when the view is populated but empty — a valid state, and
        distinct from the unpopulated one, which raises instead.

    Raises:
        StaleAnalyticsError: If the materialized views have never been populated.
        QueryNotFoundError: If the query is not registered.
        DatabaseError: On any driver failure.
    """
    return await fetch_one(session, "meta/activity_bounds") or {}


__all__ = [
    "get_activity_bounds",
    "get_dataset_counts",
]
