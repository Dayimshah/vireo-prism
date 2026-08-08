-- Churn risk score per user: a transparent additive scorecard, not a model.
--
-- Deliberately not machine learning, and that is a design decision worth defending.
-- A gradient-boosted model would score marginally better on AUC and would be
-- unexplainable to the retention team who have to act on it. This scorecard assigns
-- points for observable behaviours, so every score decomposes into the reasons
-- behind it — which is what makes an at-risk list actionable rather than merely
-- accurate.
--
-- Five weighted signals, 100 points total. Recency dominates because in every
-- consumer subscription business it is the strongest single predictor: what someone
-- did last month tells you more than any demographic attribute.
--
-- Scores are anchored to the dataset's maximum activity date (carried by
-- mv_user_lifetime), not CURRENT_DATE, so the same seed reproduces the same scores
-- however long after generation the query runs.
WITH scored AS (
    SELECT
        l.user_id,
        u.signup_date,
        l.days_since_last_active,
        l.active_days_28d,
        l.total_sessions,
        l.completion_rate,
        l.watch_seconds_28d,
        l.tenure_days,
        l.has_active_subscription,
        l.lifetime_revenue_usd,
        l.current_mrr_usd,
        co.name  AS country,
        ch.name  AS channel,
        p.name   AS persona,

        -- 1. Recency, 0-35 points. The dominant signal.
        CASE
            WHEN l.days_since_last_active >= 45 THEN 35
            WHEN l.days_since_last_active >= 30 THEN 30
            WHEN l.days_since_last_active >= 21 THEN 24
            WHEN l.days_since_last_active >= 14 THEN 17
            WHEN l.days_since_last_active >= 7  THEN 9
            ELSE 0
        END AS recency_points,

        -- 2. Frequency collapse, 0-25 points.
        CASE
            WHEN l.active_days_28d = 0 THEN 25
            WHEN l.active_days_28d = 1 THEN 20
            WHEN l.active_days_28d <= 3 THEN 14
            WHEN l.active_days_28d <= 6 THEN 8
            WHEN l.active_days_28d <= 11 THEN 3
            ELSE 0
        END AS frequency_points,

        -- 3. Engagement depth, 0-20 points. A user who starts things and abandons
        -- them is failing to find value, which precedes leaving.
        CASE
            WHEN l.completion_rate IS NULL     THEN 20   -- never started anything
            WHEN l.completion_rate < 0.20      THEN 16
            WHEN l.completion_rate < 0.35      THEN 11
            WHEN l.completion_rate < 0.50      THEN 6
            WHEN l.completion_rate < 0.65      THEN 2
            ELSE 0
        END AS engagement_points,

        -- 4. Recent watch volume, 0-12 points.
        CASE
            WHEN COALESCE(l.watch_seconds_28d, 0) = 0        THEN 12
            WHEN l.watch_seconds_28d < 1800                  THEN 9
            WHEN l.watch_seconds_28d < 7200                   THEN 5
            WHEN l.watch_seconds_28d < 21600                  THEN 2
            ELSE 0
        END AS volume_points,

        -- 5. Tenure risk, 0-8 points. Churn hazard is front-loaded: the first month
        -- is the dangerous one, and survivors get progressively safer.
        CASE
            WHEN l.tenure_days <= 30  THEN 8
            WHEN l.tenure_days <= 60  THEN 5
            WHEN l.tenure_days <= 90  THEN 3
            ELSE 0
        END AS tenure_points
    FROM analytics.mv_user_lifetime AS l
    JOIN core.users              AS u  USING (user_id)
    JOIN core.countries          AS co ON co.country_id = u.country_id
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    JOIN core.personas           AS p  ON p.persona_id  = u.persona_id
    -- Already-churned users are excluded: the point is prediction, not a list of
    -- people who have already left.
    WHERE u.churned_at IS NULL
      {{user_filter}}
),
totalled AS (
    SELECT
        *,
        (recency_points + frequency_points + engagement_points
         + volume_points + tenure_points) AS risk_score
    FROM scored
)
SELECT
    user_id,
    signup_date,
    country,
    channel,
    persona,
    risk_score,
    CASE
        WHEN risk_score >= 70 THEN 'critical'
        WHEN risk_score >= 50 THEN 'high'
        WHEN risk_score >= 30 THEN 'medium'
        ELSE 'low'
    END                                              AS risk_band,
    days_since_last_active,
    active_days_28d,
    total_sessions,
    ROUND(completion_rate::numeric, 3)               AS completion_rate,
    ROUND(COALESCE(watch_seconds_28d, 0)::numeric / 3600.0, 1) AS watch_hours_28d,
    tenure_days,
    has_active_subscription,
    ROUND(current_mrr_usd, 2)                        AS mrr_at_risk_usd,
    ROUND(lifetime_revenue_usd, 2)                   AS lifetime_revenue_usd,
    -- Component breakdown, so the UI can render driver chips per user. This is the
    -- column that turns a score into something a retention team can act on.
    recency_points,
    frequency_points,
    engagement_points,
    volume_points,
    tenure_points,
    -- Largest single contributor: the headline reason.
    CASE GREATEST(recency_points, frequency_points, engagement_points,
                  volume_points, tenure_points)
        WHEN recency_points    THEN 'dormant'
        WHEN frequency_points  THEN 'visiting less'
        WHEN engagement_points THEN 'abandoning content'
        WHEN volume_points     THEN 'low watch time'
        ELSE 'new account'
    END                                              AS primary_driver
FROM totalled
WHERE risk_score >= CAST(:min_risk_score AS int)
-- Paying users first within a risk band: equal risk, unequal revenue consequence.
ORDER BY risk_score DESC, current_mrr_usd DESC
LIMIT CAST(:limit AS int)
