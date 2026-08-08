"""Response models for the two geography and device queries.

A country league table and a device/platform breakdown. Semantics live in
:mod:`app.repositories.geo`. Nullability follows the rule set out in
:mod:`app.schemas.content`.

The country ranking is lifetime-to-date, not windowed — it takes ``date_to`` alone, and
:mod:`app.services` lists it among the functions that deliberately accept no start date.
A reader comparing it against a windowed endpoint is comparing two different questions.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class CountryRankingRow(RowModel):
    """One country's engagement and revenue, accumulated to the cut-off date.

    Attributes:
        country: Country name.
        region: Its region, e.g. ``APAC``.
        tier: Market tier as a number.
        tier_label: The same tier as a phrase, e.g. ``high volume``.
        users: Users acquired in the country.
        active_users: How many have been active.
        paying_users: How many currently pay.
        conversion_pct: Payers as a share of users.
        churn_pct: Churned users as a share of users.
        sessions: Sessions from the country.
        watch_hours: Total playback hours.
        watch_hours_per_user: Hours per acquired user.
        avg_completion_rate: Mean completion rate, as a fraction.
        avg_active_days: Mean active days per user.
        revenue_usd: Revenue to date.
        current_mrr_usd: Recurring revenue currently attributable.
        arpu_usd: Revenue per acquired user.
        arppu_usd: Revenue per *paying* user, or ``None`` where nobody pays. Four of
            the twenty seeded countries have no payers at all, so this null is the
            common case rather than an edge one.
        share_of_users_pct: Share of all users.
        share_of_watch_pct: Share of all watch hours.
        share_of_revenue_pct: Share of all revenue.
        revenue_index: Revenue share against user share. Below ``1`` means the country
            brings more attention than money — the comparison that makes this table
            worth reading rather than the raw totals.
        watch_rank: Rank by watch hours, ``1`` highest.
        revenue_rank: Rank by revenue.
        arpu_rank: Rank by revenue per user. Diverges sharply from ``watch_rank``,
            which is the finding: the largest audiences are not the most valuable ones.
    """

    country: str
    region: str
    tier: int
    tier_label: str
    users: int
    active_users: int
    paying_users: int
    conversion_pct: Number | None = None
    churn_pct: Number | None = None
    sessions: int
    watch_hours: Number
    watch_hours_per_user: Number | None = None
    avg_completion_rate: Number | None = None
    avg_active_days: Number | None = None
    revenue_usd: Number
    current_mrr_usd: Number
    arpu_usd: Number | None = None
    arppu_usd: Number | None = Field(
        default=None,
        description="Null where the country has no paying users.",
    )
    share_of_users_pct: Number | None = None
    share_of_watch_pct: Number | None = None
    share_of_revenue_pct: Number | None = None
    revenue_index: Number | None = Field(
        default=None,
        description="Revenue share over user share. Below 1 means attention without money.",
    )
    watch_rank: int
    revenue_rank: int
    arpu_rank: int


class DeviceBreakdownRow(RowModel):
    """Usage by device form factor and platform.

    Two kinds of row share this shape, distinguished by ``row_type``: one counts users
    by the device they *signed up* on, the other counts activity by the device a
    session ran on. Shares are computed within a ``row_type``, so rows of different
    types must not be summed or compared directly.

    The revenue and engagement columns are null on one of the two row kinds — half the
    rows in a live response — because revenue attaches to a user rather than to a
    session's device. A client should read those columns from the row kind that carries
    them rather than treating the nulls as missing data.

    Attributes:
        row_type: Which measure this row belongs to.
        form_factor: Device form factor, e.g. ``phone``.
        platform: Device platform, e.g. ``Android``.
        users: Users in this cell.
        sessions: Sessions in this cell.
        watch_hours: Playback hours in this cell.
        avg_session_minutes: Mean session length, or ``None`` on the row kind that does
            not carry it.
        avg_completion_rate: Mean completion rate, or ``None``.
        revenue_usd: Revenue attributable, or ``None``.
        paying_users: Paying users, or ``None``.
        conversion_pct: Payers as a share of users, or ``None``.
        share_of_users_pct: Share of users *within this row type*.
        share_of_watch_pct: Share of watch hours within this row type.
        share_of_sessions_pct: Share of sessions within this row type.
    """

    row_type: str = Field(
        description="Signup device or session device. Never sum across row types.",
    )
    form_factor: str
    platform: str
    users: int
    sessions: int
    watch_hours: Number
    avg_session_minutes: Number | None = None
    avg_completion_rate: Number | None = None
    revenue_usd: Number | None = None
    paying_users: int | None = None
    conversion_pct: Number | None = None
    share_of_users_pct: Number | None = None
    share_of_watch_pct: Number | None = None
    share_of_sessions_pct: Number | None = None


__all__ = [
    "CountryRankingRow",
    "DeviceBreakdownRow",
]
