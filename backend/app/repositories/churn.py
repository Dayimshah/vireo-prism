"""Churn: why subscribers left, and which current subscribers look likely to.

Two queries facing opposite directions in time. :func:`get_reason_mix` is
retrospective — what happened, and what it cost. :func:`get_risk_scorecard` is
prospective — who is at risk now, and why.

Voluntary versus involuntary
----------------------------
:func:`get_reason_mix` splits cancellations by ``churn_type``, and the split
matters because the two have different owners. Voluntary churn is a product and
pricing problem; involuntary churn — an expired subscription, in this schema — is a
payments problem. Reporting one blended churn rate makes a recoverable billing
failure look like dissatisfaction, which sends the wrong team to fix it.

A scorecard, not a model
------------------------
:func:`get_risk_scorecard` assigns points for observable behaviours across five
weighted signals totalling 100. That is deliberately not machine learning, and the
reasoning is worth stating: a gradient-boosted model would score marginally better
on AUC and would be unexplainable to the retention team who have to act on it.
Every score here decomposes into the reasons behind it — the query returns the
component points *and* a ``primary_driver`` label — which is what makes an at-risk
list actionable rather than merely accurate.

Recency dominates the weighting (35 of 100 points) because in consumer
subscription businesses it is the strongest single predictor: what someone did last
month tells you more than any demographic attribute.

Scores are anchored to the dataset's maximum activity date rather than
``CURRENT_DATE``, so the same seed reproduces the same scores however long after
generation the query runs. This is why the scorecard takes no date parameters at
all — it describes the current state of the user base, not a window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Default number of at-risk users to return.
DEFAULT_LIMIT: Final[int] = 100

#: Default score floor. Zero returns every non-churned user, which is rarely what
#: a retention view wants; 30 is where the query's own banding calls a user
#: "medium" risk and above.
DEFAULT_MIN_RISK_SCORE: Final[int] = 30


async def get_reason_mix(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the monthly mix of cancellation reasons, with revenue lost.

    ``early_churn_pct`` — the share of cancellations from users with 30 days or
    fewer of tenure — is the column worth watching. Early churn points at
    onboarding or at a mismatch between what was sold and what was delivered,
    which is a different problem from long-tenured users drifting away.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per month and reason, ordered by month then cancellation count
        descending, with keys ``month``, ``reason``, ``churn_type``,
        ``cancellations``, ``mrr_lost_usd``, ``avg_mrr_lost_usd``,
        ``avg_tenure_days``, ``median_tenure_days``, ``pct_of_month``,
        ``churned_within_30d`` and ``early_churn_pct``.

        ``churn_type`` is ``'involuntary'`` for expired subscriptions and
        ``'voluntary'`` otherwise. ``pct_of_month`` is normalised within each month,
        so the reasons for a single month sum to 100.
    """
    return await fetch_all(
        session,
        "churn/churn_reason_mix",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_risk_scorecard(
    session: AsyncSession,
    limit: int = DEFAULT_LIMIT,
    min_risk_score: int = DEFAULT_MIN_RISK_SCORE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return current subscribers ranked by churn risk, with score components.

    Takes no date range: the score describes the present state of each user,
    anchored to the dataset's latest activity date so results are reproducible.

    Already-churned users are excluded — the point is prediction, not a list of
    people who have already left. Within a risk band, higher-MRR users are returned
    first: equal risk carries unequal revenue consequence.

    Args:
        session: A read-only session.
        limit: Maximum users to return.
        min_risk_score: Users scoring below this are excluded. The query's own
            bands are ``low`` (<30), ``medium`` (30-49), ``high`` (50-69) and
            ``critical`` (70+).
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per at-risk user, ordered by score then MRR descending, with keys
        ``user_id``, ``signup_date``, ``country``, ``channel``, ``persona``,
        ``risk_score``, ``risk_band``, ``days_since_last_active``,
        ``active_days_28d``, ``total_sessions``, ``completion_rate``,
        ``watch_hours_28d``, ``tenure_days``, ``has_active_subscription``,
        ``mrr_at_risk_usd``, ``lifetime_revenue_usd``, the five component scores
        ``recency_points``, ``frequency_points``, ``engagement_points``,
        ``volume_points`` and ``tenure_points``, and ``primary_driver``.

        ``completion_rate`` is ``None`` for a user who has never started anything,
        which the scorecard treats as maximum engagement risk rather than as zero.
    """
    return await fetch_all(
        session,
        "churn/churn_risk_scorecard",
        {
            "limit": limit,
            "min_risk_score": min_risk_score,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MIN_RISK_SCORE",
    "get_reason_mix",
    "get_risk_scorecard",
]
