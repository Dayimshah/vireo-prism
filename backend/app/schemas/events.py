"""Response model for the event-stream composition query.

One query, one model. Semantics live in :mod:`app.repositories.events`. Nullability
follows the rule set out in :mod:`app.schemas.content`.

``screen_mix`` is the only JSONB column in the API, and the only field in this package
whose contents are not a fixed set of keys: it maps screen name to event count, and the
screens present differ per event type. It is typed ``dict[str, int]`` rather than given
a model of its own, because a model would have to enumerate screens that the data
decides — the one place in these schemas where an open shape is the honest description
rather than a shortcut.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class EventDistributionRow(RowModel):
    """One event type's share of the stream.

    Attributes:
        event_name: Event name as emitted, e.g. ``VIDEO_PROGRESS``.
        event_category: Its category, e.g. ``playback``.
        events: Times the event fired.
        sessions: Distinct sessions containing it.
        users: Distinct users who produced it.
        distinct_content: Distinct titles it touched.
        watch_hours: Playback hours associated with it.
        pct_of_events: Share of all events. ``VIDEO_PROGRESS`` dominates by design —
            it is a heartbeat rather than a user action, which is why a chart of raw
            event volume says little about behaviour.
        events_per_session: Mean occurrences per session containing it.
        pct_of_sessions_reached: Share of all sessions that contained it at least once,
            which is the figure that describes reach; ``pct_of_events`` describes
            volume, and the two rank event types very differently.
        screen_mix: Screen name to event count for this event type. Keys vary by event.
    """

    event_name: str
    event_category: str
    events: int
    sessions: int
    users: int
    distinct_content: int
    watch_hours: Number
    pct_of_events: Number | None = None
    events_per_session: Number | None = None
    pct_of_sessions_reached: Number | None = Field(
        default=None,
        description="Reach, not volume. Ranks event types differently from pct_of_events.",
    )
    screen_mix: dict[str, int] = Field(
        default_factory=dict,
        description="Screen name to count. Keys are data-dependent, not a fixed set.",
    )


__all__ = ["EventDistributionRow"]
