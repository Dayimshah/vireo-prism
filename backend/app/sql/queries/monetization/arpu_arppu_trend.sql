-- ARPU and ARPPU over time, with the paying share that connects them.
--
-- Two metrics that are constantly confused, and the confusion matters commercially:
--
--   ARPU   revenue / all active users        moves with conversion *and* pricing
--   ARPPU  revenue / paying users only       moves with pricing and plan mix only
--
-- Reported together with paying_share_pct, because ARPU = ARPPU x paying share. If
-- ARPU falls while ARPPU holds, the problem is conversion, not price. Publishing
-- only one of the two invites exactly the wrong conclusion.
--
-- Both are computed monthly against users active in that month, not against the
-- whole registered base. Counting dormant accounts in the denominator would make
-- ARPU decline mechanically as the service ages, which says nothing about the
-- business.
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
active AS (
    SELECT
        m.month,
        COUNT(DISTINCT d.user_id) AS active_users
    FROM months AS m
    JOIN analytics.mv_user_daily AS d
      ON d.activity_date >= m.month
     AND d.activity_date < (m.month + INTERVAL '1 month')
    JOIN scoped_users USING (user_id)
    GROUP BY m.month
),
revenue AS (
    SELECT
        m.month,
        SUM(s.mrr_usd)                AS mrr,
        COUNT(DISTINCT s.user_id)     AS paying_users
    FROM months AS m
    JOIN core.subscriptions AS s
      ON s.started_on < (m.month + INTERVAL '1 month')
     AND COALESCE(s.ended_on, DATE '9999-12-31') >= m.month
     AND s.mrr_usd > 0
    JOIN scoped_users USING (user_id)
    GROUP BY m.month
),
plan_mix AS (
    -- Average price point of the active base. Isolates plan-mix shift from pure
    -- price changes: ARPPU can rise because users upgrade or because prices rose,
    -- and these are different stories.
    SELECT
        m.month,
        AVG(pl.monthly_price_usd) AS avg_list_price
    FROM months AS m
    JOIN core.subscriptions AS s
      ON s.started_on < (m.month + INTERVAL '1 month')
     AND COALESCE(s.ended_on, DATE '9999-12-31') >= m.month
     AND s.mrr_usd > 0
    JOIN core.subscription_plans AS pl ON pl.plan_id = s.plan_id
    JOIN scoped_users USING (user_id)
    GROUP BY m.month
)
SELECT
    m.month,
    COALESCE(a.active_users, 0)::bigint                              AS active_users,
    COALESCE(r.paying_users, 0)::bigint                              AS paying_users,
    ROUND(COALESCE(r.mrr, 0), 2)                                     AS mrr_usd,
    ROUND(COALESCE(r.mrr, 0) / NULLIF(a.active_users, 0), 2)         AS arpu_usd,
    ROUND(COALESCE(r.mrr, 0) / NULLIF(r.paying_users, 0), 2)         AS arppu_usd,
    ROUND(100.0 * r.paying_users / NULLIF(a.active_users, 0), 2)     AS paying_share_pct,
    ROUND(p.avg_list_price::numeric, 2)                              AS avg_list_price_usd,
    -- Realised MRR below list price is the billing-cadence discount: annual
    -- subscribers pay less per month, which is why annual mix shows up as an
    -- apparent price decline while being a retention win.
    ROUND(
        100.0 * (COALESCE(r.mrr, 0) / NULLIF(r.paying_users, 0))
        / NULLIF(p.avg_list_price, 0), 1
    )                                                                AS realised_vs_list_pct,
    ROUND(
        COALESCE(r.mrr, 0) / NULLIF(a.active_users, 0)
        - LAG(COALESCE(r.mrr, 0) / NULLIF(a.active_users, 0)) OVER (ORDER BY m.month), 2
    )                                                                AS arpu_change_usd
FROM months AS m
LEFT JOIN active   AS a USING (month)
LEFT JOIN revenue  AS r USING (month)
LEFT JOIN plan_mix AS p USING (month)
ORDER BY m.month
