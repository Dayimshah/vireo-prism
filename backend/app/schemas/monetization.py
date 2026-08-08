"""Response models for the four monetization queries.

ARPU/ARPPU trend, the MRR movement waterfall, conversion by watch-time decile, and
trial conversion by plan. Semantics live in :mod:`app.repositories.monetization`.
Nullability follows the rule set out in :mod:`app.schemas.content`.

Every model here is month-grained except the decile breakdown. MRR is a *stock* — a
recurring rate at a point in time — so months cannot be summed, which is why
:mod:`app.services.overview` reports the latest month rather than a window total.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime

from pydantic import Field

from app.schemas.base import Number, RowModel


class ArpuTrendRow(RowModel):
    """One month of revenue per user and per paying user.

    Attributes:
        month: First day of the month.
        active_users: Users active in the month, the ARPU denominator.
        paying_users: Users with an active subscription, the ARPPU denominator.
        mrr_usd: Recurring revenue for the month.
        arpu_usd: ``mrr_usd / active_users``.
        arppu_usd: ``mrr_usd / paying_users``, or ``None`` in a month with no payers.
            Always at least ARPU, and usually far larger — reading the two as if they
            were the same metric is the standard way to overstate monetisation.
        paying_share_pct: Payers as a share of active users, or ``None`` with no
            active users.
        avg_list_price_usd: Mean list price of the plans held, or ``None`` with no
            payers.
        realised_vs_list_pct: Realised revenue against list price, which reveals
            discounting and annual-plan effects. ``None`` with no payers.
        arpu_change_usd: Change in ARPU from the previous month, or ``None`` for the
            first month in the series — there is nothing before it to compare with.
    """

    month: date
    active_users: int
    paying_users: int
    mrr_usd: Number
    arpu_usd: Number
    arppu_usd: Number | None = Field(
        default=None,
        description="Per paying user. Always >= ARPU; not interchangeable with it.",
    )
    paying_share_pct: Number | None = None
    avg_list_price_usd: Number | None = None
    realised_vs_list_pct: Number | None = None
    arpu_change_usd: Number | None = Field(
        default=None,
        description="Null for the first month: nothing precedes it.",
    )


class MrrMovementRow(RowModel):
    """One month of the MRR waterfall, decomposed by movement type.

    ``opening_mrr`` plus the five movement columns equals ``closing_mrr``. The movement
    columns are null rather than zero in a month where that movement did not occur,
    which is a reporting convention worth knowing before summing them: treat null as
    zero when reconciling the waterfall, and as "did not happen" when reading it.

    Attributes:
        month: First day of the month.
        opening_mrr: MRR entering the month.
        new_mrr: Added by first-time subscribers.
        reactivation_mrr: Added by returning subscribers, or ``None`` if none
            reactivated.
        expansion_mrr: Added by upgrades, or ``None``.
        contraction_mrr: Lost to downgrades, negative, or ``None``.
        churn_mrr: Lost to cancellations, negative, or ``None``.
        closing_mrr: MRR leaving the month.
        net_change_mrr: ``closing_mrr - opening_mrr``.
        new_subscribers: Count of first-time subscribers.
        churned_subscribers: Count of cancellations.
        reactivated_subscribers: Count of returning subscribers.
        net_revenue_retention_pct: Revenue retained from the opening cohort including
            expansion, so it can exceed 100. ``None`` when opening MRR is zero.
    """

    month: date
    opening_mrr: Number
    new_mrr: Number
    reactivation_mrr: Number | None = None
    expansion_mrr: Number | None = None
    contraction_mrr: Number | None = Field(
        default=None,
        description="Negative when present. Null means no downgrades that month.",
    )
    churn_mrr: Number | None = Field(
        default=None,
        description="Negative when present. Null means no cancellations that month.",
    )
    closing_mrr: Number
    net_change_mrr: Number
    new_subscribers: int
    churned_subscribers: int
    reactivated_subscribers: int
    net_revenue_retention_pct: Number | None = Field(
        default=None,
        description="Can exceed 100 when expansion outweighs churn. Null with no opening MRR.",
    )


class WatchDecileConversionRow(RowModel):
    """Conversion rate by watch-time decile.

    The clearest evidence in the dataset that engagement precedes payment: conversion
    runs monotonically from the lowest decile to the highest. That relationship was
    built into the generator via a logistic model and is recovered here from the event
    stream, which is what makes it a check on the pipeline rather than a coincidence.

    Attributes:
        watch_decile: Decile of watch time, ``1`` lowest.
        users: Users in the decile.
        min_watch_hours: Lower bound of the decile.
        max_watch_hours: Upper bound.
        avg_watch_hours: Mean within the decile.
        avg_completions: Mean completed titles.
        avg_sessions: Mean sessions.
        started_trial: Users who began a trial.
        converted_paid: Users who became paying.
        still_paying: Users still paying at the window's end.
        trial_rate_pct: Trial starts as a share of the decile.
        conversion_pct: Conversions as a share of the decile.
        paid_retention_pct: ``still_paying / converted_paid``, or ``None`` where
            nobody converted.
        conversion_lift: This decile's conversion against the overall rate.
    """

    watch_decile: int
    users: int
    min_watch_hours: Number
    max_watch_hours: Number
    avg_watch_hours: Number
    avg_completions: Number
    avg_sessions: Number
    started_trial: int
    converted_paid: int
    still_paying: int
    trial_rate_pct: Number | None = None
    conversion_pct: Number | None = None
    paid_retention_pct: Number | None = None
    conversion_lift: Number | None = None


class TrialConversionRow(RowModel):
    """Trial-to-paid conversion for one plan.

    Attributes:
        trial_plan: Plan the trial was taken on.
        plan_tier: That plan's tier.
        list_price_usd: Its list price.
        trials_started: Trials begun on the plan.
        trials_converted: Trials that became paid.
        conversion_pct: ``trials_converted / trials_started`` as a percentage.
        avg_days_to_convert: Mean days from trial start to payment, or ``None`` where
            nobody converted.
        median_days_to_convert: The median of the same, or ``None``.
        switched_plan: Converters who ended up on a different plan from the one they
            trialled.
        avg_converted_mrr_usd: Mean MRR per converter, or ``None``.
        total_converted_mrr_usd: Total MRR from converters, or ``None``.
        chose_annual: Converters who chose annual billing.
        still_paying: Converters still paying at the window's end.
        post_conversion_retention_pct: ``still_paying / trials_converted``, or
            ``None``.
    """

    trial_plan: str
    plan_tier: str
    list_price_usd: Number
    trials_started: int
    trials_converted: int
    conversion_pct: Number | None = None
    avg_days_to_convert: Number | None = None
    median_days_to_convert: Number | None = None
    switched_plan: int
    avg_converted_mrr_usd: Number | None = None
    total_converted_mrr_usd: Number | None = None
    chose_annual: int
    still_paying: int
    post_conversion_retention_pct: Number | None = None


__all__ = [
    "ArpuTrendRow",
    "MrrMovementRow",
    "TrialConversionRow",
    "WatchDecileConversionRow",
]
