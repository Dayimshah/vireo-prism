"""Response models for the two churn queries.

A monthly reason mix, and a per-user risk scorecard. Semantics live in
:mod:`app.repositories.churn`. Nullability follows the rule set out in
:mod:`app.schemas.content`.

The scorecard is the only endpoint in the API that returns rows about identifiable
individuals — one per user, with a ``user_id``. On synthetic data that is a portfolio
feature rather than a privacy question, and it is worth stating that the distinction is
noticed: the same endpoint over real users would need access control, and this project
has none by design (see :mod:`app.core.security`).
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime

from pydantic import Field

from app.schemas.base import Number, RowModel


class ChurnReasonRow(RowModel):
    """One cancellation reason in one month.

    Attributes:
        month: First day of the month.
        reason: Cancellation reason as recorded.
        churn_type: Whether the cancellation was voluntary or involuntary. Involuntary
            churn — a failed payment — is a different problem from a user choosing to
            leave, and mixing the two makes both look unfixable.
        cancellations: Cancellations for this reason.
        mrr_lost_usd: Recurring revenue lost.
        avg_mrr_lost_usd: Mean revenue lost per cancellation.
        avg_tenure_days: Mean tenure at cancellation.
        median_tenure_days: Median tenure, which is the more robust of the two.
        pct_of_month: Share of the month's cancellations.
        churned_within_30d: Cancellations from users who had subscribed less than 30
            days earlier.
        early_churn_pct: Those as a share of this reason — high early churn points at
            the acquisition promise rather than the product.
    """

    month: date
    reason: str
    churn_type: str = Field(
        description="Voluntary or involuntary. Involuntary churn is a payments problem.",
    )
    cancellations: int
    mrr_lost_usd: Number
    avg_mrr_lost_usd: Number | None = None
    avg_tenure_days: Number | None = None
    median_tenure_days: Number | None = None
    pct_of_month: Number | None = None
    churned_within_30d: int
    early_churn_pct: Number | None = None


class ChurnRiskRow(RowModel):
    """One at-risk user, with the components of their score.

    The five ``*_points`` columns sum to ``risk_score``. They are returned rather than
    just the total so a reader can see *why* a user scores highly — a dormant user and
    a lightly-engaged one can reach the same score and need different interventions,
    and ``primary_driver`` names the largest contributor.

    Attributes:
        user_id: Surrogate key for the user.
        signup_date: When they signed up.
        country: Their country.
        channel: The channel that acquired them.
        persona: Their assigned persona.
        risk_score: Total risk score; higher is worse.
        risk_band: Bucketed reading of the score, e.g. ``critical``.
        days_since_last_active: Days since their last session.
        active_days_28d: Active days in the trailing 28.
        total_sessions: Lifetime sessions.
        completion_rate: Lifetime completion rate, as a fraction rather than a
            percentage.
        watch_hours_28d: Watch hours in the trailing 28 days.
        tenure_days: Days since signup.
        has_active_subscription: Whether they are currently paying.
        mrr_at_risk_usd: Recurring revenue that would be lost, zero for a non-payer.
            This is what makes the list actionable: a high-risk non-payer costs
            nothing to lose.
        lifetime_revenue_usd: Revenue from this user to date.
        recency_points: Score contribution from dormancy.
        frequency_points: Contribution from how often they visit.
        engagement_points: Contribution from completion behaviour.
        volume_points: Contribution from watch volume.
        tenure_points: Contribution from tenure.
        primary_driver: Which component contributed most.
    """

    user_id: int
    signup_date: date
    country: str
    channel: str
    persona: str
    risk_score: int
    risk_band: str
    days_since_last_active: int
    active_days_28d: int
    total_sessions: int
    completion_rate: Number
    watch_hours_28d: Number
    tenure_days: int
    has_active_subscription: bool
    mrr_at_risk_usd: Number = Field(
        description="Zero for a non-payer. What makes a high score worth acting on, or not.",
    )
    lifetime_revenue_usd: Number
    recency_points: int
    frequency_points: int
    engagement_points: int
    volume_points: int
    tenure_points: int
    primary_driver: str


__all__ = [
    "ChurnReasonRow",
    "ChurnRiskRow",
]
