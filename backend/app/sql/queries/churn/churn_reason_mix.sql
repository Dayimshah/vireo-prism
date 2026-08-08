-- Cancellation reasons over time, separating voluntary from involuntary churn.
--
-- The split is the point, and most churn reporting omits it. A user who cancelled
-- chose to leave; a user whose payment failed did not. They are the same row in a
-- subscriptions table and completely different problems: the first needs product or
-- pricing work, the second needs a dunning fix and is often the cheapest churn to
-- recover.
--
-- Reported monthly rather than in aggregate, because reason mix shifts. A spike in
-- "too expensive" following a price change is a finding; the same reason at a steady
-- baseline is just the cost of doing business.
WITH cancellations AS (
    SELECT
        date_trunc('month', s.ended_on)::date AS month,
        s.status,
        COALESCE(s.cancel_reason, 'unspecified') AS reason,
        s.mrr_usd,
        s.started_on,
        s.ended_on,
        (s.ended_on - s.started_on)              AS tenure_days,
        pl.tier                                  AS plan_tier
    FROM core.subscriptions AS s
    JOIN core.users AS u USING (user_id)
    JOIN core.subscription_plans AS pl ON pl.plan_id = s.plan_id
    WHERE s.ended_on IS NOT NULL
      AND s.ended_on BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      AND s.mrr_usd > 0            -- exclude trial expiries; those are a separate funnel
      {{user_filter}}
)
SELECT
    month,
    reason,
    -- 'expired' means payment lapsed rather than a deliberate cancellation.
    CASE WHEN status = 'expired' THEN 'involuntary' ELSE 'voluntary' END AS churn_type,
    COUNT(*)::bigint                                                    AS cancellations,
    ROUND(SUM(mrr_usd), 2)                                              AS mrr_lost_usd,
    ROUND(AVG(mrr_usd), 2)                                              AS avg_mrr_lost_usd,
    ROUND(AVG(tenure_days)::numeric, 0)                                 AS avg_tenure_days,
    ROUND(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tenure_days)::numeric, 0
    )                                                                   AS median_tenure_days,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY month), 2)
                                                                        AS pct_of_month,
    -- Early churn signals an onboarding or expectation-setting failure rather than
    -- gradual disengagement, so it warrants a different response.
    COUNT(*) FILTER (WHERE tenure_days <= 30)::bigint                   AS churned_within_30d,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE tenure_days <= 30) / NULLIF(COUNT(*), 0), 1
    )                                                                   AS early_churn_pct
FROM cancellations
GROUP BY month, reason, churn_type
ORDER BY month, cancellations DESC
