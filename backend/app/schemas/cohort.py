"""Response models for the four cohort queries.

Two retention matrices, cumulative revenue by cohort, and LTV by acquisition channel.
Semantics live in :mod:`app.repositories.cohort`. Nullability follows the rule set out
in :mod:`app.schemas.content`.

The null in a matrix cell is the point, not a gap
-------------------------------------------------
``active_users`` and ``retention_pct`` are null for any cell whose period has not fully
elapsed inside the observation window — a cohort that signed up last month cannot have
a month-3 figure yet. Roughly a third of the cells in a live matrix are null for this
reason.

Zero would be a lie there, and a specific one: a heatmap would paint the bottom-right
triangle as total churn, which is the shape a real churn problem also makes. The
distinction is right-censoring, and ``is_complete`` is the column that states it, so a
client can render those cells as blank rather than as dark red.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime

from pydantic import Field

from app.schemas.base import Number, RowModel


class MonthlyMatrixRow(RowModel):
    """One cell of the monthly signup-cohort retention matrix.

    Attributes:
        cohort_month: First day of the month the cohort signed up in.
        month_n: Months since signup; ``0`` is the signup month itself.
        cohort_size: Users in the cohort. Constant across a cohort's row, so
            percentages within one row are comparable.
        is_complete: Whether this cell's month elapsed entirely inside the observation
            window. ``False`` means the figures below are null.
        active_users: Users from the cohort active in month N, or ``None`` when the
            cell is not yet observable.
        retention_pct: ``active_users / cohort_size`` as a percentage, or ``None`` for
            the same reason.
    """

    cohort_month: date
    month_n: int
    cohort_size: int
    is_complete: bool = Field(
        description="False means the period has not fully elapsed and the figures are null.",
    )
    active_users: int | None = None
    retention_pct: Number | None = Field(
        default=None,
        description="Null means not yet observable. Render blank, never as zero retention.",
    )


class WeeklyMatrixRow(RowModel):
    """One cell of the weekly signup-cohort retention matrix.

    The weekly grain of :class:`MonthlyMatrixRow`; the same censoring rule applies.

    Attributes:
        cohort_week: First day of the week the cohort signed up in.
        week_n: Weeks since signup; ``0`` is the signup week.
        cohort_size: Users in the cohort.
        is_complete: Whether the week elapsed entirely inside the observation window.
        active_users: Users active in week N, or ``None`` when not yet observable.
        retention_pct: Retention for the cell, or ``None``.
    """

    cohort_week: date
    week_n: int
    cohort_size: int
    is_complete: bool
    active_users: int | None = None
    retention_pct: Number | None = None


class RevenueCumulativeRow(RowModel):
    """Cumulative revenue for one cohort at one month of age.

    Attributes:
        cohort_month: First day of the cohort's signup month.
        month_n: Months since signup.
        cohort_size: Users in the cohort.
        revenue_usd: Revenue recognised in month N alone.
        cumulative_revenue_usd: Revenue from signup through month N.
        cumulative_arpu_usd: ``cumulative_revenue_usd / cohort_size`` — per *acquired*
            user, not per payer, so it is the figure to compare against CAC.
    """

    cohort_month: date
    month_n: int
    cohort_size: int
    revenue_usd: Number
    cumulative_revenue_usd: Number
    cumulative_arpu_usd: Number = Field(
        description="Per acquired user, not per payer. Comparable with CAC.",
    )


class LtvByChannelRow(RowModel):
    """Lifetime value against acquisition cost, per channel.

    Attributes:
        channel: Marketing channel name.
        channel_group: Its group, e.g. ``Paid`` or ``Owned``.
        is_paid: Whether the channel costs money.
        users_acquired: Users attributed to the channel.
        users_converted: How many became paying subscribers.
        conversion_pct: ``users_converted / users_acquired`` as a percentage.
        cac_usd: Cost to acquire one user through this channel. Zero for organic
            channels, which is why the ratios below can be null rather than infinite.
        total_revenue_usd: Revenue from the cohort to date.
        ltv_per_acquired_usd: Revenue per acquired user.
        revenue_per_payer_usd: Revenue per *converting* user, or ``None`` when nobody
            converted.
        ltv_to_cac_ratio: ``ltv_per_acquired_usd / cac_usd``, or ``None`` when CAC is
            zero. Undefined rather than infinite — an organic channel has no ratio, and
            a very large number would sort to the top of a league table and be read as
            the best-performing channel.
        total_spend_usd: Total spend attributed to the channel.
        net_contribution_usd: ``total_revenue_usd - total_spend_usd``.
    """

    channel: str
    channel_group: str
    is_paid: bool
    users_acquired: int
    users_converted: int
    conversion_pct: Number | None = None
    cac_usd: Number
    total_revenue_usd: Number
    ltv_per_acquired_usd: Number
    revenue_per_payer_usd: Number | None = None
    ltv_to_cac_ratio: Number | None = Field(
        default=None,
        description="Null when CAC is zero: undefined, not infinite.",
    )
    total_spend_usd: Number
    net_contribution_usd: Number


__all__ = [
    "LtvByChannelRow",
    "MonthlyMatrixRow",
    "RevenueCumulativeRow",
    "WeeklyMatrixRow",
]
