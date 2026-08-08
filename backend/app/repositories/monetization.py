"""Revenue: ARPU trends, MRR movement, trial conversion and the paywall funnel.

Four queries over ``core.subscriptions``, which holds full subscription *history*
rather than current state. A user who subscribed, cancelled and returned has three
rows, and that is what makes :func:`get_mrr_movement` computable rather than
approximated — new, expansion, contraction, churn and reactivation are each
derivable only if the history is intact.

Two distinctions the numbers here depend on:

**ARPU vs ARPPU.** :func:`get_arpu_trend` returns both. ARPU divides revenue by
*all* active users, ARPPU by *paying* users only. ARPPU is always the larger
number, and quoting it as "ARPU" overstates monetisation by whatever the free
share happens to be. Both are returned so neither can be mistaken for the other.

**List price vs realised MRR.** ``mrr_usd`` is realised revenue after the
billing-period discount, so annual subscribers contribute lower monthly MRR and
higher lifetime value. ``realised_vs_list_pct`` reports the gap explicitly rather
than leaving a reader to wonder why MRR divided by subscribers does not match the
price list.

:func:`get_conversion_by_watch_decile` is the query that recovers the simulation's
planted conversion model. The seeder drives subscription through a logistic
function of watch behaviour; this query reads no coefficients — it buckets users by
observed watch time and reports conversion per bucket. The monotonic climb across
deciles is therefore a recovered finding, and its steepness is the evidence that
engagement causes conversion in this dataset rather than merely correlating with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.repositories.base import FilterSet, fetch_all

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

#: Plans with fewer trials than this are omitted from trial-conversion output.
#: A plan with three trials reports 0% or 33% or 67%, none of which is a rate.
DEFAULT_MIN_COHORT_SIZE: Final[int] = 30


async def get_arpu_trend(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return monthly ARPU and ARPPU with the paying share.

    Both averages are returned deliberately: ARPU over all active users, ARPPU over
    paying users only. ``paying_share_pct`` is the bridge between them, so a change
    in ARPU can be attributed to pricing or to mix rather than being ambiguous.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per month, ordered ascending, with keys ``month``,
        ``active_users``, ``paying_users``, ``mrr_usd``, ``arpu_usd``,
        ``arppu_usd``, ``paying_share_pct``, ``avg_list_price_usd``,
        ``realised_vs_list_pct`` and ``arpu_change_usd``.

        ``arpu_change_usd`` is the month-over-month delta and is ``None`` on the
        first month, which has no predecessor.
    """
    return await fetch_all(
        session,
        "monetization/arpu_arppu_trend",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_mrr_movement(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return the monthly MRR movement waterfall.

    Decomposes the change in recurring revenue into its five causes rather than
    reporting a net figure. A flat month is not necessarily a quiet one: heavy new
    revenue offset by heavy churn is a retention problem invisible in the net
    number, and this is the query that exposes it.

    ``net_revenue_retention_pct`` is the headline health metric — expansion and
    reactivation net of contraction and churn, as a percentage of opening MRR.
    Above 100% means the existing base grew without new customers.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per month, ordered ascending, with keys ``month``,
        ``opening_mrr``, ``new_mrr``, ``reactivation_mrr``, ``expansion_mrr``,
        ``contraction_mrr``, ``churn_mrr``, ``closing_mrr``, ``net_change_mrr``,
        ``new_subscribers``, ``churned_subscribers``, ``reactivated_subscribers``
        and ``net_revenue_retention_pct``.

        ``contraction_mrr`` and ``churn_mrr`` are reported as positive magnitudes;
        subtract them from the opening balance rather than adding. The components
        reconcile: opening plus new, reactivation and expansion, less contraction
        and churn, equals closing.
    """
    return await fetch_all(
        session,
        "monetization/mrr_movement_waterfall",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_conversion_by_watch_decile(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return subscription conversion by watch-time decile.

    The evidence that engagement drives conversion in this dataset. Users are
    bucketed by observed watch time and conversion is reported per bucket; the
    query reads none of the generator's coefficients, so the monotonic climb across
    deciles is recovered from the event stream rather than asserted.

    ``conversion_lift`` expresses each decile against the overall rate, which is
    what makes the shape legible: the top decile converts many times the average,
    and the bottom deciles barely convert at all.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per decile, ordered ascending, with keys ``watch_decile``,
        ``users``, ``min_watch_hours``, ``max_watch_hours``, ``avg_watch_hours``,
        ``avg_completions``, ``avg_sessions``, ``started_trial``,
        ``converted_paid``, ``still_paying``, ``trial_rate_pct``,
        ``conversion_pct``, ``paid_retention_pct`` and ``conversion_lift``.
    """
    return await fetch_all(
        session,
        "monetization/subscription_conversion_funnel",
        {
            "date_from": date_from,
            "date_to": date_to,
            **(filters or FilterSet()).as_params(),
        },
    )


async def get_trial_conversion_by_plan(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    filters: FilterSet | None = None,
) -> list[dict[str, Any]]:
    """Return trial-to-paid conversion for each plan a trial started on.

    Reports both the mean and median days to convert, because the mean is pulled by
    users who convert late while most convert at trial expiry. ``switched_plan``
    counts users who converted onto a *different* plan than they trialled, which is
    the signal that a trial tier is mispositioned rather than unattractive.

    Args:
        session: A read-only session.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        min_cohort_size: Plans with fewer trials than this are omitted.
        filters: Optional user-scope filters. Unfiltered when omitted.

    Returns:
        One row per trial plan with keys ``trial_plan``, ``plan_tier``,
        ``list_price_usd``, ``trials_started``, ``trials_converted``,
        ``conversion_pct``, ``avg_days_to_convert``, ``median_days_to_convert``,
        ``switched_plan``, ``avg_converted_mrr_usd``, ``total_converted_mrr_usd``,
        ``chose_annual``, ``still_paying`` and ``post_conversion_retention_pct``.
    """
    return await fetch_all(
        session,
        "monetization/trial_conversion_by_plan",
        {
            "date_from": date_from,
            "date_to": date_to,
            "min_cohort_size": min_cohort_size,
            **(filters or FilterSet()).as_params(),
        },
    )


__all__ = [
    "DEFAULT_MIN_COHORT_SIZE",
    "get_arpu_trend",
    "get_conversion_by_watch_decile",
    "get_mrr_movement",
    "get_trial_conversion_by_plan",
]
