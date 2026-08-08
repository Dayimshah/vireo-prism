"""Response models for the three marketing queries.

Channel attribution, LTV against CAC, and payback period. Semantics live in
:mod:`app.repositories.marketing`. Nullability follows the rule set out in
:mod:`app.schemas.content`.

All three carry a ratio against acquisition cost, and all three return ``None`` rather
than a number when that cost is zero. Organic channels have no CAC, so they have no
ratio — and a very large stand-in would sort straight to the top of a league table and
be read as the best-performing channel, which is the specific misreading the null
prevents.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class ChannelAttributionRow(RowModel):
    """One acquisition channel, from spend through to revenue.

    Attributes:
        channel: Marketing channel name.
        channel_group: Its group, e.g. ``Paid`` or ``Organic``.
        is_paid: Whether the channel costs money.
        cac_usd: Cost to acquire one user. Zero for organic channels.
        users_acquired: Users attributed to the channel.
        never_activated: Acquired users who never had a session — the clearest signal
            of low-quality traffic, and invisible in a conversion rate alone.
        never_activated_pct: Those users as a share of the channel.
        avg_sessions: Mean sessions per acquired user.
        avg_watch_hours: Mean watch hours per acquired user.
        avg_completion_rate: Mean completion rate among them.
        avg_titles_watched: Mean distinct titles watched.
        converted_users: Users who became paying subscribers.
        conversion_pct: Converters as a share of the channel.
        churned_users: Users who have since churned.
        churn_pct: Churned users as a share of the channel.
        total_revenue_usd: Revenue from the channel to date.
        current_mrr_usd: Recurring revenue currently attributable to it.
        total_spend_usd: Total spend on the channel.
        net_contribution_usd: ``total_revenue_usd - total_spend_usd``.
        share_of_users_pct: Share of all acquired users.
        share_of_revenue_pct: Share of all revenue. Comparing this against
            ``share_of_users_pct`` is what separates a channel that brings volume from
            one that brings value.
    """

    channel: str
    channel_group: str
    is_paid: bool
    cac_usd: Number
    users_acquired: int
    never_activated: int = Field(
        description="Acquired but never had a session. A conversion rate alone hides these.",
    )
    never_activated_pct: Number | None = None
    avg_sessions: Number | None = None
    avg_watch_hours: Number | None = None
    avg_completion_rate: Number | None = None
    avg_titles_watched: Number | None = None
    converted_users: int
    conversion_pct: Number | None = None
    churned_users: int
    churn_pct: Number | None = None
    total_revenue_usd: Number
    current_mrr_usd: Number
    total_spend_usd: Number
    net_contribution_usd: Number
    share_of_users_pct: Number | None = None
    share_of_revenue_pct: Number | None = None


class LtvToCacRow(RowModel):
    """One channel's lifetime value against its acquisition cost.

    Attributes:
        channel: Marketing channel name.
        channel_group: Its group.
        is_paid: Whether the channel costs money.
        users_acquired: Users attributed to the channel.
        converted: How many became paying.
        conversion_pct: Converters as a share of the channel.
        cac_usd: Cost per acquired user.
        ltv_per_user_usd: Revenue per acquired user to date. To date, not projected —
            a young channel looks worse than it will turn out to be, and no
            extrapolation is applied here to make it look better.
        total_revenue_usd: Revenue from the channel.
        total_spend_usd: Spend on the channel.
        ltv_to_cac_ratio: ``ltv_per_user_usd / cac_usd``, or ``None`` when CAC is zero.
        avg_watch_hours: Mean watch hours per acquired user.
        avg_completion_rate: Mean completion rate.
        median_ltv_usd: Median revenue per user, which is far below the mean wherever a
            few heavy payers dominate.
        median_cac_usd: Median acquisition cost.
        quadrant: Which quadrant of the LTV/CAC plot the channel falls in, e.g.
            ``organic``.
        is_profitable: Whether revenue to date exceeds spend.
    """

    channel: str
    channel_group: str
    is_paid: bool
    users_acquired: int
    converted: int
    conversion_pct: Number | None = None
    cac_usd: Number
    ltv_per_user_usd: Number
    total_revenue_usd: Number
    total_spend_usd: Number
    ltv_to_cac_ratio: Number | None = Field(
        default=None,
        description="Null when CAC is zero: undefined, not infinite.",
    )
    avg_watch_hours: Number | None = None
    avg_completion_rate: Number | None = None
    median_ltv_usd: Number | None = None
    median_cac_usd: Number | None = None
    quadrant: str
    is_profitable: bool


class CacPaybackRow(RowModel):
    """How long a channel takes to repay its acquisition cost.

    Attributes:
        channel: Marketing channel name.
        channel_group: Its group.
        is_paid: Whether the channel costs money.
        users_acquired: Users attributed to the channel.
        cac_per_user_usd: Cost per acquired user.
        total_spend_usd: Spend on the channel.
        revenue_to_date_usd: Revenue recovered so far.
        net_position_usd: Revenue less spend.
        payback_months: Months until cumulative revenue covered CAC, or ``None`` when
            that has not happened yet. Null is right-censoring, not a zero: the
            channel may still pay back next month, and a client should render it as
            "not yet" rather than as an instant payback.
        payback_band: Bucketed reading of the same, e.g. ``slow (<= 18 months)``.
        revenue_per_user_usd: Revenue per acquired user.
        ltv_to_cac_ratio: Revenue per user against cost per user, or ``None`` when CAC
            is zero.
    """

    channel: str
    channel_group: str
    is_paid: bool
    users_acquired: int
    cac_per_user_usd: Number
    total_spend_usd: Number
    revenue_to_date_usd: Number
    net_position_usd: Number
    payback_months: int | None = Field(
        default=None,
        description="Null means payback has not been reached yet, not that it was instant.",
    )
    payback_band: str
    revenue_per_user_usd: Number
    ltv_to_cac_ratio: Number | None = None


__all__ = [
    "CacPaybackRow",
    "ChannelAttributionRow",
    "LtvToCacRow",
]
