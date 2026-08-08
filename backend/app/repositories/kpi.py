"""Headline engagement KPIs: DAU, WAU, MAU, stickiness and daily composition.

The six queries behind the dashboard's top row. Every one of them returns a
gap-free daily series over the requested window — the underlying SQL LEFT JOINs a
date spine — so a day with no activity arrives as an explicit zero rather than as
a missing row. Callers can plot the result directly without filling gaps, and a
dip in the chart is a real dip.

All six read ``analytics.mv_user_daily`` rather than ``core.events``. The view is
already one row per user per active day, which turns DAU from a ``COUNT
DISTINCT`` over a million event rows into a ``COUNT`` over a narrow pre-aggregated
one. That is the entire reason the materialized views exist, and it is why these
endpoints answer in tens of milliseconds.

Windows are rolling, not calendar-aligned: WAU counts distinct users across each
day and the six before it, MAU across each day and the twenty-seven before it.
Calendar-week WAU steps every Monday and calendar-month MAU is distorted by
February, and neither artefact is something a reader should have to mentally
correct for. The trade-off is that the first rows of a window read from activity
*before* ``date_from`` — the SQL widens its own scan to cover that, so the
leading edge of the series is correct rather than artificially low.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


async def get_dau(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return daily active users with session and watch-time context.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per day in the window, ordered ascending, with keys ``day``,
        ``dau``, ``sessions``, ``watch_seconds`` and ``watch_minutes_per_user``.
        Quiet days are present with zero counts; ``watch_minutes_per_user`` is
        ``None`` on a day with no active users, because a per-user average over
        zero users is undefined rather than zero.
    """
    return await fetch_all(
        session,
        "kpi/dau",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_wau(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return weekly active users on a 7-day rolling basis.

    Each row counts the distinct users active on that day or in the preceding six,
    so the series is smooth and a mid-week change is visible instead of being
    absorbed into a Monday step.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per day, ordered ascending, with keys ``day`` and ``wau``.
    """
    return await fetch_all(
        session,
        "kpi/wau",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_mau(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return monthly active users on a 28-day rolling basis.

    Twenty-eight days rather than a calendar month, so every point spans exactly
    four weeks and the series is not distorted by month length.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per day, ordered ascending, with keys ``day`` and ``mau``.
    """
    return await fetch_all(
        session,
        "kpi/mau",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_stickiness(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return DAU as a percentage of rolling MAU.

    The scale-free engagement ratio: it reads as "how many days in four weeks does
    an active user show up", so it cannot be inflated by acquisition the way
    absolute DAU can. Streaming products sit well below 50%; a value above that
    would indicate a calculation fault rather than a triumph.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per day, ordered ascending, with keys ``day``, ``dau``, ``mau``
        and ``stickiness_pct``. The percentage is ``None`` where MAU is zero.
    """
    return await fetch_all(
        session,
        "kpi/stickiness_dau_mau",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_new_vs_returning(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the daily split of new, returning and resurrected active users.

    Three buckets rather than two, and the third is the informative one: a user
    dormant for more than 28 days who comes back is a win-back, not a returning
    user. Folding them together would conceal both the lapse and the recovery.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per day, ordered ascending, with keys ``day``, ``new_users``,
        ``returning_users``, ``resurrected_users`` and ``total_active``. The three
        buckets are mutually exclusive and sum to ``total_active``.
    """
    return await fetch_all(
        session,
        "kpi/new_vs_returning_daily",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_sessions_per_user(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return sessions per active user per day, with the distribution around it.

    The mean is reported alongside the median and p90 because session counts are
    right-skewed: a few heavy users pull the mean above what a typical user does,
    and only showing the mean turns "most users open the app once" into "our users
    average three sessions a day".

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per day, ordered ascending, with keys ``day``, ``active_users``,
        ``total_sessions``, ``mean_sessions_per_user``,
        ``median_sessions_per_user``, ``p90_sessions_per_user`` and
        ``mean_events_per_user``.
    """
    return await fetch_all(
        session,
        "kpi/sessions_per_user_daily",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "get_dau",
    "get_mau",
    "get_new_vs_returning",
    "get_sessions_per_user",
    "get_stickiness",
    "get_wau",
]
