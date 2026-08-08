"""Raw event-stream composition: volume and reach per event type.

One query, and the closest thing in the API to a look at the clickstream itself.
Everything else in this package reads a materialized view or an aggregate; this
counts the fifteen event types in ``core.events`` directly and reports how each is
distributed.

``event_category`` is derived in SQL rather than in the frontend — navigation,
discovery, playback, conversion. Putting that classification next to the enum it
describes means it cannot drift from the ``event_name`` values declared in Alembic
revision 0001, which is exactly what would happen if a TypeScript constant owned
the mapping instead.

``pct_of_sessions_reached`` uses ``OPEN_APP`` as its denominator, since that event
occurs in every session by construction. It therefore reads as funnel reach: the
share of all sessions in which this event happened at all, which is a different
and more useful measure than the share of total events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


async def get_event_distribution(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return volume, reach and screen mix for every event type.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per event name, ordered by event count descending, with keys
        ``event_name``, ``event_category``, ``events``, ``sessions``, ``users``,
        ``distinct_content``, ``watch_hours``, ``pct_of_events``,
        ``events_per_session``, ``pct_of_sessions_reached`` and ``screen_mix``.

        ``screen_mix`` is a JSON object mapping screen name to occurrence count,
        returned already decoded as a ``dict``. It is included so one request serves
        both the event bar chart and the per-event screen drilldown, rather than the
        frontend issuing a request per event name.

        ``distinct_content`` is zero for events that carry no ``content_id`` —
        ``OPEN_APP``, ``HOME``, ``SEARCH`` and ``EXIT`` — which the schema enforces
        rather than merely expects.
    """
    return await fetch_all(
        session,
        "events/event_distribution",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = ["get_event_distribution"]
