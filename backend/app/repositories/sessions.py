"""Session shape: duration, depth, composition, timing and device switching.

Where the KPI module counts *how many* sessions happen, this one describes *what
they look like*. Every function here reports a distribution rather than a mean,
because session metrics are heavily right-skewed and a mean describes a session
almost nobody has: a handful of multi-hour TV binges pull the average well above
the median, so "our average session is 34 minutes" can be true while most
sessions are under ten.

Two pairs of functions look similar and are not:

* :func:`get_events_per_session` counts interaction *volume*;
  :func:`get_session_depth` measures *progress*. A user can generate thirty events
  scrolling the home rails without reaching a title — deep by volume, shallow by
  progress. The gap between the two is where indecision shows up.
* :func:`get_session_duration_percentiles` returns an overall row *and* per
  form-factor rows in one result set, distinguished by ``dimension_type``, so the
  headline and the breakdown never disagree about their denominator.

Four of the six read ``analytics.mv_funnel_steps`` or ``core.sessions`` directly
rather than the event table, which is what keeps them fast over a million-plus
events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


async def get_session_duration_percentiles(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return session duration percentiles, overall and by device form factor.

    Percentiles rather than an average, because the distribution is right-skewed
    and the mean sits above the median by a wide margin. ``watch_share_pct`` is the
    fraction of session time actually spent watching; the remainder is browsing,
    deciding and paused playback.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        Rows keyed by ``dimension_type`` and ``dimension``: one
        ``dimension_type='form_factor'`` row per device form factor, then a single
        ``dimension_type='overall'`` row. Each carries ``sessions``,
        ``mean_minutes``, ``p25_minutes``, ``median_minutes``, ``p75_minutes``,
        ``p90_minutes``, ``p99_minutes``, ``mean_watch_minutes``, ``mean_events``
        and ``watch_share_pct``.

        Select the headline row by filtering on ``dimension_type == 'overall'``,
        not by position. The query orders by ``dimension_type`` ascending, which
        places ``'form_factor'`` before ``'overall'`` alphabetically — the opposite
        of what that file's own comment claims. Relying on the row's index would
        silently pick a single form factor as the headline figure.
    """
    return await fetch_all(
        session,
        "sessions/session_duration_percentiles",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_session_depth(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the distribution of how far into a session users get.

    Five ordered depth levels, from "opened only" to "completed something".
    ``pct_reaching_at_least`` is the cumulative share reaching each depth or
    deeper, which is the same data read as a funnel.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per depth level, ordered shallowest first, with keys
        ``depth_level``, ``depth_label``, ``sessions``, ``pct_of_sessions``,
        ``avg_events``, ``avg_max_step``, ``avg_watch_minutes`` and
        ``pct_reaching_at_least``.
    """
    return await fetch_all(
        session,
        "sessions/session_depth_distribution",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_events_per_session(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the bucketed distribution of events per session.

    Buckets separate behaviours that mean different things rather than dividing the
    range evenly: a two-event session is a bounce, three to five is a browse, and
    past twenty is genuine viewing.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per bucket with keys ``bucket``, ``bucket_order``, ``sessions``,
        ``pct_of_sessions``, ``avg_watch_minutes`` and ``pct_with_playback``. Rows
        arrive in ``bucket_order`` — sort on that rather than on ``bucket``, whose
        labels sort lexically into nonsense.
    """
    return await fetch_all(
        session,
        "sessions/events_per_session_dist",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_entry_exit_screens(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return where sessions begin and end, as entry/exit screen pairs.

    The exit half carries the product signal, and the query labels it: leaving from
    ``player`` is a satisfied user, from ``paywall`` a blocked one, from ``search``
    an unfulfilled intent. Those are the same log event and three different
    problems, so ``exit_signal`` classifies them rather than leaving the frontend
    to encode that judgement in a colour scale.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per entry/exit pair, ordered by session count descending, with keys
        ``entry_screen``, ``exit_screen``, ``sessions``, ``pct_of_all``,
        ``pct_of_entry_screen``, ``mean_minutes``, ``watch_hours``,
        ``first_sessions`` and ``exit_signal``.
    """
    return await fetch_all(
        session,
        "sessions/session_entry_exit_screens",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_device_switching(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return cross-device behaviour: switches between surfaces, and breadth.

    Two measures in one result set, distinguished by ``row_type``. Transition rows
    are directional and show migration patterns — phone-to-TV is someone settling
    in for the evening. Breadth rows count how many distinct devices each user
    uses at all. ``pct_within_type`` is normalised inside each ``row_type``, so the
    two groups are each read as their own distribution and never summed together.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        Rows with keys ``row_type``, ``label``, ``observations``, ``users`` and
        ``pct_within_type``, ordered by type then user count descending.
    """
    return await fetch_all(
        session,
        "sessions/session_device_switching",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_activity_heatmap(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return session volume by day of week and hour of day.

    Both a UTC hour and a local hour are returned, and the distinction matters for
    honest presentation. Sessions are generated in each user's local evening and
    stored as UTC, so plotting ``hour_utc`` across a twenty-country user base shows
    a broad plateau rather than a real evening peak — the peak exists, it is just
    smeared across time zones. ``hour_local`` re-derives the user's own clock via
    fixed per-country offsets and is the column that shows the genuine daily
    rhythm. Prefer it for any "when do people watch" chart, and use ``hour_utc``
    only for capacity questions, which really are a UTC concern.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per ``(weekday_utc, hour_utc, hour_local)`` combination, ordered by
        weekday then UTC hour, with keys ``weekday_utc`` (0 = Sunday, matching
        PostgreSQL's ``EXTRACT(DOW)``), ``hour_utc``, ``hour_local``, ``sessions``,
        ``unique_users``, ``avg_duration_minutes`` and ``watch_seconds``.
    """
    return await fetch_all(
        session,
        "sessions/activity_hour_weekday_heatmap",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "get_activity_heatmap",
    "get_device_switching",
    "get_entry_exit_screens",
    "get_events_per_session",
    "get_session_depth",
    "get_session_duration_percentiles",
]
