"""User segmentation: RFM deciles rolled up into named behavioural segments.

One query, and the only one in the package that takes neither a date range nor any
other argument beyond the optional filters. That is deliberate: RFM describes the
*current* state of the user base, scored against the dataset's latest activity
date, so a window parameter would imply a time-slicing this metric does not support.

RFM adapted for streaming
-------------------------
The classic retail triple is recency, frequency, monetary value. Applied to a
subscription product it needs one adjustment worth stating: monetary value is
largely determined by plan price and tenure rather than by discretionary purchases,
so it varies far less between users than it does in retail. Frequency and recency
therefore carry most of the discriminating power here, which is why the segment
definitions lean on them and why ``engaged but unmonetised`` exists as a category at
all — a user can score at the top on behaviour and at the bottom on revenue, and in
retail RFM that combination is rare while here it is a large and commercially
interesting group.

Deciles are computed across the whole user base, so they are relative rankings, not
absolute thresholds. A "champion" is in the top band *of this dataset*; the segment
names describe position within the population rather than an external benchmark.

The six segments are ``champions``, ``loyal``, ``at risk (high value)``, ``lost``,
``new or promising`` and ``engaged but unmonetised``. They are mutually exclusive
and cover every scored user, so ``pct_of_users`` sums to 100.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_rfm_segments(
    session: AsyncSession,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the user base rolled up into named RFM segments.

    Args:
        session: A read-only session.
        filters: Optional user-scope filters. Unfiltered when omitted. Note the
            deciles behind the segments are computed over the *filtered*
            population, so a filtered result re-ranks within that subset rather
            than reporting where those users sit in the whole base.

    Returns:
        One row per segment, ordered by total revenue descending, with keys
        ``rfm_segment``, ``users``, ``pct_of_users``, ``avg_recency_decile``,
        ``avg_frequency_decile``, ``avg_monetary_decile``, ``avg_days_dormant``,
        ``avg_sessions``, ``avg_watch_hours``, ``avg_titles_watched``,
        ``avg_genres``, ``total_revenue_usd``, ``avg_revenue_usd``,
        ``current_mrr_usd``, ``pct_of_revenue``, ``premium_users`` and
        ``premium_share_pct``.

        Reading ``pct_of_users`` against ``pct_of_revenue`` is the point of the
        table: a segment holding a small share of users and a large share of revenue
        is where retention spending belongs.
    """
    return await fetch_all(
        session,
        "users/power_users_rfm_decile",
        (filters or FilterSet()).as_params(),
    )


__all__ = ["get_rfm_segments"]
