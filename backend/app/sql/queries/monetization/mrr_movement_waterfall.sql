-- MRR movement waterfall: new, expansion, contraction, churn, reactivation.
--
-- The single most scrutinised chart in any subscription business, and the one most
-- often computed wrongly. The decomposition must reconcile: opening MRR plus every
-- movement must equal closing MRR exactly, or the chart is fiction. That constraint
-- is what forces the approach below.
--
-- Per-user monthly MRR is built first, then each month is compared against the
-- previous one for the same user. The five categories then follow from the pair:
--
--   new           0 -> positive, and no prior paid history
--   reactivation  0 -> positive, with prior paid history
--   expansion     positive -> higher (upgrade)
--   contraction   positive -> lower  (downgrade)
--   churn         positive -> 0
--
-- Modelling plan changes as two subscription rows in the seeder is what makes
-- expansion and contraction visible at all; an in-place plan update would erase the
-- movement and leave a waterfall with only three bars.
WITH months AS (
    SELECT generate_series(
        date_trunc('month', CAST(:date_from AS date)),
        date_trunc('month', CAST(:date_to AS date)),
        INTERVAL '1 month'
    )::date AS month
),
scoped_users AS (
    SELECT u.user_id FROM core.users AS u WHERE TRUE {{user_filter}}
),
-- MRR per user per month, from subscriptions active at any point in that month.
user_month AS (
    SELECT
        m.month,
        s.user_id,
        SUM(s.mrr_usd) AS mrr
    FROM months AS m
    JOIN core.subscriptions AS s
      ON s.started_on < (m.month + INTERVAL '1 month')
     AND COALESCE(s.ended_on, DATE '9999-12-31') >= m.month
     AND s.mrr_usd > 0
    JOIN scoped_users USING (user_id)
    GROUP BY m.month, s.user_id
),
-- Every (month, user) pair where the user had MRR in this month or the previous,
-- so a drop to zero is represented rather than simply absent.
grid AS (
    SELECT m.month, u.user_id
    FROM months AS m
    CROSS JOIN (SELECT DISTINCT user_id FROM user_month) AS u
),
paired AS (
    SELECT
        g.month,
        g.user_id,
        COALESCE(cur.mrr, 0)  AS current_mrr,
        COALESCE(prv.mrr, 0)  AS previous_mrr,
        -- Prior paid history distinguishes a genuinely new subscriber from a
        -- returning one. Without it, every win-back is miscounted as new growth.
        EXISTS (
            SELECT 1 FROM user_month AS h
            WHERE h.user_id = g.user_id AND h.month < (g.month - INTERVAL '1 month')
        ) AS had_history
    FROM grid AS g
    LEFT JOIN user_month AS cur ON cur.month = g.month              AND cur.user_id = g.user_id
    LEFT JOIN user_month AS prv ON prv.month = g.month - INTERVAL '1 month'
                               AND prv.user_id = g.user_id
),
classified AS (
    SELECT
        month,
        CASE
            WHEN previous_mrr = 0 AND current_mrr > 0 AND NOT had_history THEN 'new'
            WHEN previous_mrr = 0 AND current_mrr > 0 AND had_history     THEN 'reactivation'
            WHEN previous_mrr > 0 AND current_mrr > previous_mrr          THEN 'expansion'
            WHEN previous_mrr > 0 AND current_mrr < previous_mrr
                                 AND current_mrr > 0                     THEN 'contraction'
            WHEN previous_mrr > 0 AND current_mrr = 0                    THEN 'churn'
            ELSE 'unchanged'
        END                              AS movement,
        (current_mrr - previous_mrr)     AS delta,
        current_mrr,
        previous_mrr
    FROM paired
    WHERE current_mrr > 0 OR previous_mrr > 0
)
SELECT
    month,
    ROUND(SUM(previous_mrr), 2)                                             AS opening_mrr,
    ROUND(SUM(delta) FILTER (WHERE movement = 'new'), 2)                    AS new_mrr,
    ROUND(SUM(delta) FILTER (WHERE movement = 'reactivation'), 2)           AS reactivation_mrr,
    ROUND(SUM(delta) FILTER (WHERE movement = 'expansion'), 2)              AS expansion_mrr,
    -- Negative by construction; kept signed so the bars sum correctly.
    ROUND(SUM(delta) FILTER (WHERE movement = 'contraction'), 2)            AS contraction_mrr,
    ROUND(SUM(delta) FILTER (WHERE movement = 'churn'), 2)                  AS churn_mrr,
    ROUND(SUM(current_mrr), 2)                                              AS closing_mrr,
    ROUND(SUM(delta), 2)                                                    AS net_change_mrr,
    COUNT(*) FILTER (WHERE movement = 'new')::bigint                        AS new_subscribers,
    COUNT(*) FILTER (WHERE movement = 'churn')::bigint                      AS churned_subscribers,
    COUNT(*) FILTER (WHERE movement = 'reactivation')::bigint               AS reactivated_subscribers,
    -- Net revenue retention: the health metric that matters more than gross churn,
    -- because expansion can offset losses. Above 100% means the existing base is
    -- growing without any new acquisition.
    ROUND(
        100.0 * (SUM(previous_mrr) + SUM(delta) FILTER (WHERE movement IN
                 ('expansion', 'contraction', 'churn')))
        / NULLIF(SUM(previous_mrr), 0), 2
    )                                                                       AS net_revenue_retention_pct
FROM classified
GROUP BY month
ORDER BY month
