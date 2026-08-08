"""Geography and device: country rankings and platform breakdowns.

Two queries covering "where are our users" and "what are they watching on".

Country tiers are a monetisation band, not a size
-------------------------------------------------
``tier`` on ``core.countries`` runs 1 (high ARPU, high CAC) to 3 (high volume, low
ARPU), and :func:`get_country_ranking` returns ``tier_label`` so the ranking is
readable without consulting the dimension table. The three rank columns —
``watch_rank``, ``revenue_rank``, ``arpu_rank`` — usually disagree, and that
disagreement is the finding: the market with the most watch time is rarely the one
with the highest revenue per user. ``revenue_index`` expresses the same tension as a
single number, being a country's revenue share divided by its user share, where
above 1.0 means it monetises better than its size implies.

Signup device versus session device
-----------------------------------
:func:`get_device_breakdown` returns two blocks of rows distinguished by
``row_type``, and conflating them would be a real error:

* ``'signup'`` groups by the device recorded on ``core.users`` — the surface a user
  first arrived on. Revenue and conversion attach to the *user*, so this is the
  block to read for "which platform acquires paying customers".
* ``'usage'`` groups by the device on ``core.sessions`` — the surface actually used
  for each session. Watch time and session length attach to the *session*, so this
  is the block for "where does viewing happen".

A user who signs up on a phone and watches on a TV appears under phone in the first
block and TV in the second. Both are correct; they answer different questions. Every
share column is normalised within its own ``row_type``, so the two blocks each sum
to 100 independently and must never be added together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Countries with fewer users than this are omitted from the ranking.
DEFAULT_MIN_COHORT_SIZE: Final[int] = 30


async def get_country_ranking(
    session: AsyncSession,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return per-country engagement and revenue, ranked three ways.

    Takes only an end date, not a range. This is a lifetime-to-date ranking: every
    figure accumulates from each user's signup up to ``date_to``, because comparing
    markets on a narrow window would penalise those Vireo entered recently. There is
    deliberately no ``date_from`` parameter rather than one that silently does
    nothing.

    Args:
        session: A read-only session.
        date_to: Cut-off date; all activity and revenue up to and including this day
            is counted.
        min_cohort_size: Countries with fewer users than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per country, ordered by watch hours descending, with keys
        ``country``, ``region``, ``tier``, ``tier_label``, ``users``,
        ``active_users``, ``paying_users``, ``conversion_pct``, ``churn_pct``,
        ``sessions``, ``watch_hours``, ``watch_hours_per_user``,
        ``avg_completion_rate``, ``avg_active_days``, ``revenue_usd``,
        ``current_mrr_usd``, ``arpu_usd``, ``arppu_usd``, ``share_of_users_pct``,
        ``share_of_watch_pct``, ``share_of_revenue_pct``, ``revenue_index``,
        ``watch_rank``, ``revenue_rank`` and ``arpu_rank``.

        ``arpu_usd`` divides revenue by all users; ``arppu_usd`` by paying users
        only. The latter is always larger and is not interchangeable with the former.
    """
    return await fetch_all(
        session,
        "geo/country_engagement_ranking",
        {
            "date_to": date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_device_breakdown(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return engagement and revenue by device, on both signup and usage grain.

    Returns two blocks in one result set — see the module docstring for why the
    ``row_type`` distinction matters. Filter on ``row_type`` before charting;
    plotting both blocks together double-counts users.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        Rows ordered by ``row_type`` then watch hours descending, with keys
        ``row_type`` (``'signup'`` or ``'usage'``), ``form_factor``, ``platform``,
        ``users``, ``sessions``, ``watch_hours``, ``avg_session_minutes``,
        ``avg_completion_rate``, ``revenue_usd``, ``paying_users``,
        ``conversion_pct``, ``share_of_users_pct``, ``share_of_watch_pct`` and
        ``share_of_sessions_pct``.

        Share columns are normalised within each ``row_type``.
    """
    return await fetch_all(
        session,
        "geo/device_platform_breakdown",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "get_country_ranking",
    "get_device_breakdown",
]
