-- Country ranking across engagement and monetisation, with the tier contrast.
--
-- The chart that shows why a single "growth" number misleads. Tier-3 markets
-- (India, Brazil, Indonesia) contribute the most users and watch hours while
-- earning the least per user; tier-1 markets are the reverse. Both facts are true
-- simultaneously and a business needs to see them together.
--
-- This is a planted signal recovered: seeder/config.py declares
-- CONVERSION_COUNTRY_TIER_EFFECT and PLAN_WEIGHTS_BY_TIER, and nothing in this query
-- reads either. It groups by a foreign key and divides revenue by users.
--
-- ARPU is per *acquired* user, not per payer. Per-payer figures would hide the
-- conversion difference between tiers, which is half of the ARPU gap.
WITH cohort AS (
    SELECT
        u.user_id,
        u.is_premium,
        u.churned_at,
        co.country_id,
        co.name    AS country,
        co.region,
        co.tier
    FROM core.users AS u
    JOIN core.countries AS co ON co.country_id = u.country_id
    WHERE u.signup_date <= CAST(:date_to AS date)
      {{user_filter}}
),
engagement AS (
    SELECT
        c.country_id,
        COUNT(*)                                          AS users,
        COUNT(l.user_id)                                  AS active_users,
        SUM(COALESCE(l.total_sessions, 0))                AS sessions,
        SUM(COALESCE(l.total_watch_seconds, 0))           AS watch_seconds,
        SUM(COALESCE(l.completed_videos, 0))              AS completions,
        AVG(l.completion_rate)                            AS avg_completion_rate,
        AVG(COALESCE(l.active_days, 0))                   AS avg_active_days,
        SUM(COALESCE(l.lifetime_revenue_usd, 0))          AS revenue_usd,
        SUM(COALESCE(l.current_mrr_usd, 0))               AS current_mrr_usd,
        COUNT(*) FILTER (WHERE l.lifetime_revenue_usd > 0) AS paying_users,
        COUNT(*) FILTER (WHERE c.churned_at IS NOT NULL)   AS churned_users
    FROM cohort AS c
    LEFT JOIN analytics.mv_user_lifetime AS l USING (user_id)
    GROUP BY c.country_id
)
SELECT
    c.country,
    c.region,
    c.tier,
    CASE c.tier
        WHEN 1 THEN 'high ARPU'
        WHEN 2 THEN 'mid'
        ELSE 'high volume'
    END                                                              AS tier_label,
    e.users::bigint,
    e.active_users::bigint,
    e.paying_users::bigint,
    ROUND(100.0 * e.paying_users / NULLIF(e.users, 0), 2)            AS conversion_pct,
    ROUND(100.0 * e.churned_users / NULLIF(e.users, 0), 2)           AS churn_pct,
    e.sessions::bigint,
    ROUND(e.watch_seconds::numeric / 3600.0, 1)                      AS watch_hours,
    ROUND(e.watch_seconds::numeric / 3600.0 / NULLIF(e.users, 0), 2) AS watch_hours_per_user,
    ROUND(e.avg_completion_rate::numeric, 3)                         AS avg_completion_rate,
    ROUND(e.avg_active_days::numeric, 1)                             AS avg_active_days,
    ROUND(e.revenue_usd, 2)                                          AS revenue_usd,
    ROUND(e.current_mrr_usd, 2)                                      AS current_mrr_usd,
    -- The two ARPU figures whose divergence is the whole point.
    ROUND(e.revenue_usd / NULLIF(e.users, 0), 2)                     AS arpu_usd,
    ROUND(e.revenue_usd / NULLIF(e.paying_users, 0), 2)              AS arppu_usd,
    ROUND(100.0 * e.users / SUM(e.users) OVER (), 2)                 AS share_of_users_pct,
    ROUND(
        100.0 * e.watch_seconds / NULLIF(SUM(e.watch_seconds) OVER (), 0), 2
    )                                                                AS share_of_watch_pct,
    ROUND(
        100.0 * e.revenue_usd / NULLIF(SUM(e.revenue_usd) OVER (), 0), 2
    )                                                                AS share_of_revenue_pct,
    -- Revenue share divided by user share. Below 1 means the market
    -- under-monetises relative to its size; above 1 means it over-delivers.
    ROUND(
        (e.revenue_usd / NULLIF(SUM(e.revenue_usd) OVER (), 0))
        / NULLIF(e.users::numeric / SUM(e.users) OVER (), 0), 2
    )                                                                AS revenue_index,
    RANK() OVER (ORDER BY e.watch_seconds DESC)                      AS watch_rank,
    RANK() OVER (ORDER BY e.revenue_usd DESC)                        AS revenue_rank,
    RANK() OVER (ORDER BY e.revenue_usd / NULLIF(e.users, 0) DESC)   AS arpu_rank
FROM engagement AS e
JOIN cohort AS c ON c.country_id = e.country_id
GROUP BY c.country, c.region, c.tier, e.users, e.active_users, e.paying_users,
         e.churned_users, e.sessions, e.watch_seconds, e.avg_completion_rate,
         e.avg_active_days, e.revenue_usd, e.current_mrr_usd
HAVING e.users >= CAST(:min_cohort_size AS int)
ORDER BY watch_hours DESC
