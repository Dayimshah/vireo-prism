"""Response model for RFM segmentation.

One query, one model. Semantics live in :mod:`app.repositories.users`.

Two properties of this endpoint are worth knowing before reading its numbers, both
documented in :mod:`app.services`:

It takes **no date range**. RFM describes the present state of the user base, anchored
to the dataset's latest activity date, and a window parameter would imply a
time-slicing it does not support.

Deciles are computed over the **filtered** population. Requesting
``?country=India`` re-ranks users within India rather than showing where Indian users
fall in the global ranking, so ``champions`` means "top decile among the users you
asked about". That is the more useful default for a filtered dashboard and the more
surprising one, so it is stated here rather than left to be inferred from a shifting
segment mix.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class RfmSegmentRow(RowModel):
    """One RFM segment and its aggregate behaviour.

    Attributes:
        rfm_segment: Segment name, e.g. ``champions``.
        users: Users in the segment.
        pct_of_users: Share of the population being described.
        avg_recency_decile: Mean recency decile, ``10`` being the most recently active.
        avg_frequency_decile: Mean frequency decile.
        avg_monetary_decile: Mean monetary decile.
        avg_days_dormant: Mean days since last activity.
        avg_sessions: Mean lifetime sessions.
        avg_watch_hours: Mean lifetime watch hours.
        avg_titles_watched: Mean distinct titles watched.
        avg_genres: Mean distinct genres watched.
        total_revenue_usd: Revenue from the segment.
        avg_revenue_usd: Revenue per user in the segment.
        current_mrr_usd: Recurring revenue currently attributable.
        pct_of_revenue: Share of all revenue. Comparing this against ``pct_of_users``
            is the whole point of the segmentation — in the seeded data champions are
            around a fifth of users and over four fifths of revenue.
        premium_users: Users in the segment on a premium tier.
        premium_share_pct: Those as a share of the segment.
    """

    rfm_segment: str
    users: int
    pct_of_users: Number | None = None
    avg_recency_decile: Number | None = None
    avg_frequency_decile: Number | None = None
    avg_monetary_decile: Number | None = None
    avg_days_dormant: Number | None = None
    avg_sessions: Number | None = None
    avg_watch_hours: Number | None = None
    avg_titles_watched: Number | None = None
    avg_genres: Number | None = None
    total_revenue_usd: Number
    avg_revenue_usd: Number | None = None
    current_mrr_usd: Number
    pct_of_revenue: Number | None = Field(
        default=None,
        description="Read against pct_of_users. The gap between them is the finding.",
    )
    premium_users: int
    premium_share_pct: Number | None = None


__all__ = ["RfmSegmentRow"]
