"""Response models for the six session-behaviour queries.

Duration percentiles, depth, event counts, entry/exit screens, device switching and an
activity heatmap. Semantics live in :mod:`app.repositories.sessions`.

One trap is worth repeating here rather than leaving in the repository, because it is a
property of the *rows* a client receives: :class:`SessionDurationPercentileRow` mixes
two kinds of row in one result. ``dimension_type`` is ``'overall'`` for the headline row
and a dimension name for the breakdown rows, and the SQL sorts on ``dimension_type``
ascending, which puts ``'form_factor'`` before ``'overall'`` because ``f`` < ``o``. A
client must select the headline row by ``dimension_type == 'overall'`` and never by
position. The comment in the frozen ``.sql`` file claims otherwise and is wrong; it is
recorded as a known defect rather than edited.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class SessionDurationPercentileRow(RowModel):
    """Session-duration percentiles, overall and by dimension.

    Attributes:
        dimension_type: ``'overall'`` for the headline row, otherwise the dimension
            being broken down. Select the headline row by this value, not by index —
            see the module docstring.
        dimension: The value within ``dimension_type``; equal to ``'all'`` on the
            headline row.
        sessions: Sessions counted.
        mean_minutes: Mean session length.
        p25_minutes: 25th percentile session length.
        median_minutes: Median session length.
        p75_minutes: 75th percentile.
        p90_minutes: 90th percentile.
        p99_minutes: 99th percentile.
        mean_watch_minutes: Mean minutes of actual playback per session, which is at
            most ``mean_minutes`` and usually less.
        mean_events: Mean events per session.
        watch_share_pct: Share of session time spent in playback.
    """

    dimension_type: str = Field(
        description="'overall' for the headline row. Never select that row by position.",
    )
    dimension: str
    sessions: int
    mean_minutes: Number
    p25_minutes: Number
    median_minutes: Number
    p75_minutes: Number
    p90_minutes: Number
    p99_minutes: Number
    mean_watch_minutes: Number
    mean_events: Number
    watch_share_pct: Number


class SessionDepthRow(RowModel):
    """Sessions bucketed by how far into the funnel they reached.

    Attributes:
        depth_level: Ordinal depth, ascending.
        depth_label: Human-readable label for the level.
        sessions: Sessions whose deepest step was this level.
        pct_of_sessions: Share of all sessions at exactly this depth.
        avg_events: Mean events in such a session.
        avg_max_step: Mean deepest funnel step reached.
        avg_watch_minutes: Mean playback minutes.
        pct_reaching_at_least: Cumulative share reaching this depth *or deeper*, so
            this column descends while ``pct_of_sessions`` does not.
    """

    depth_level: int
    depth_label: str
    sessions: int
    pct_of_sessions: Number
    avg_events: Number
    avg_max_step: Number
    avg_watch_minutes: Number
    pct_reaching_at_least: Number = Field(
        description="Cumulative: this depth or deeper. Not the same as pct_of_sessions.",
    )


class EventsPerSessionRow(RowModel):
    """Sessions bucketed by event count.

    Attributes:
        bucket: Bucket label, e.g. ``'2 (bounce)'``.
        bucket_order: Sort key, since the labels are not lexically ordered.
        sessions: Sessions in the bucket.
        pct_of_sessions: Share of all sessions.
        avg_watch_minutes: Mean playback minutes in the bucket.
        pct_with_playback: Share of the bucket's sessions containing any playback.
    """

    bucket: str
    bucket_order: int
    sessions: int
    pct_of_sessions: Number
    avg_watch_minutes: Number
    pct_with_playback: Number


class EntryExitScreenRow(RowModel):
    """Entry/exit screen pairs.

    Attributes:
        entry_screen: Screen the session began on.
        exit_screen: Screen it ended on.
        sessions: Sessions following this path.
        pct_of_all: Share of all sessions.
        pct_of_entry_screen: Share of sessions that *started* on ``entry_screen``,
            which is the conditional figure and the one worth reading for a funnel.
        mean_minutes: Mean session length on this path.
        watch_hours: Total playback hours on this path.
        first_sessions: How many were a user's very first session.
        exit_signal: Whether ending here reads as satisfied or abandoned.
    """

    entry_screen: str
    exit_screen: str
    sessions: int
    pct_of_all: Number
    pct_of_entry_screen: Number
    mean_minutes: Number
    watch_hours: Number
    first_sessions: int
    exit_signal: str


class DeviceSwitchingRow(RowModel):
    """Cross-device behaviour, in two row kinds distinguished by ``row_type``.

    Attributes:
        row_type: Which measure this row belongs to — device breadth, or switching
            within a session. Percentages are shares *within* a ``row_type``, so rows
            of different types must not be summed together.
        label: The bucket within ``row_type``.
        observations: Occurrences counted.
        users: Distinct users contributing.
        pct_within_type: Share within this ``row_type`` only.
    """

    row_type: str
    label: str
    observations: int
    users: int
    pct_within_type: Number = Field(
        description="Share within this row_type. Rows of different types do not sum to 100.",
    )


class ActivityHeatmapRow(RowModel):
    """Sessions by weekday and hour.

    Attributes:
        weekday_utc: Day of week in UTC, ``0`` = Sunday.
        hour_utc: Hour of day in UTC.
        hour_local: The same hour shifted to the user's local offset, which is what a
            "when do people watch" chart should plot.
        sessions: Sessions started in this cell.
        unique_users: Distinct users in this cell.
        avg_duration_minutes: Mean session length in this cell.
        watch_seconds: Total playback seconds in this cell.
    """

    weekday_utc: int
    hour_utc: int
    hour_local: int
    sessions: int
    unique_users: int
    avg_duration_minutes: Number
    watch_seconds: int


__all__ = [
    "ActivityHeatmapRow",
    "DeviceSwitchingRow",
    "EntryExitScreenRow",
    "EventsPerSessionRow",
    "SessionDepthRow",
    "SessionDurationPercentileRow",
]
