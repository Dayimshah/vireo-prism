-- RFM segmentation: recency, frequency and monetary value per user.
--
-- The classic retail segmentation applied to streaming, and it earns its place
-- because the three axes disagree in informative ways. A user can be highly frequent
-- and worthless (free tier, watches constantly), or dormant and valuable (annual
-- subscriber who has stopped showing up — the most urgent save in the business).
-- Ranking on any single axis hides both.
--
-- Deciles rather than absolute thresholds, so the segments stay populated at any
-- profile size and the boundaries move with the population instead of being
-- hard-coded guesses.
--
-- Recency is scored *inverted*: fewer days since last active is better, so decile 10
-- is the most recent. Getting that backwards is the standard RFM bug and it silently
-- inverts every conclusion drawn from the chart.
WITH scored AS (
    SELECT
        l.user_id,
        u.signup_date,
        co.name AS country,
        ch.name AS channel,
        p.name  AS persona,
        u.is_premium,
        l.days_since_last_active,
        l.total_sessions,
        l.active_days,
        l.total_watch_seconds,
        l.completed_videos,
        l.distinct_content,
        l.distinct_genres,
        l.lifetime_revenue_usd,
        l.current_mrr_usd,
        l.tenure_days,

        -- Inverted so 10 = most recent.
        NTILE(10) OVER (ORDER BY l.days_since_last_active DESC) AS recency_decile,
        NTILE(10) OVER (ORDER BY l.total_sessions)              AS frequency_decile,
        -- Watch time stands in for monetary value on the free tier, where revenue is
        -- zero but engagement still represents ad inventory and future conversion
        -- potential. Ranking purely on revenue would collapse 90% of users into one
        -- indistinguishable block.
        NTILE(10) OVER (ORDER BY l.lifetime_revenue_usd, l.total_watch_seconds)
                                                               AS monetary_decile
    FROM analytics.mv_user_lifetime AS l
    JOIN core.users              AS u  USING (user_id)
    JOIN core.countries          AS co ON co.country_id = u.country_id
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    JOIN core.personas           AS p  ON p.persona_id  = u.persona_id
    WHERE TRUE
      {{user_filter}}
),
segmented AS (
    SELECT
        *,
        (recency_decile + frequency_decile + monetary_decile) AS rfm_total,
        CASE
            WHEN recency_decile >= 8 AND frequency_decile >= 8 AND monetary_decile >= 8
                THEN 'champions'
            WHEN recency_decile >= 7 AND frequency_decile >= 6
                THEN 'loyal'
            -- The commercially urgent segment: they were valuable and have gone quiet.
            WHEN recency_decile <= 4 AND monetary_decile >= 7
                THEN 'at risk (high value)'
            WHEN recency_decile <= 3 AND frequency_decile <= 3
                THEN 'lost'
            WHEN recency_decile >= 8 AND frequency_decile <= 4
                THEN 'new or promising'
            WHEN frequency_decile >= 7 AND monetary_decile <= 3
                THEN 'engaged but unmonetised'
            ELSE 'middle'
        END AS rfm_segment
    FROM scored
)
SELECT
    rfm_segment,
    COUNT(*)::bigint                                                 AS users,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)               AS pct_of_users,
    ROUND(AVG(recency_decile)::numeric, 1)                           AS avg_recency_decile,
    ROUND(AVG(frequency_decile)::numeric, 1)                         AS avg_frequency_decile,
    ROUND(AVG(monetary_decile)::numeric, 1)                          AS avg_monetary_decile,
    ROUND(AVG(days_since_last_active)::numeric, 0)                   AS avg_days_dormant,
    ROUND(AVG(total_sessions)::numeric, 1)                           AS avg_sessions,
    ROUND(AVG(total_watch_seconds)::numeric / 3600.0, 1)             AS avg_watch_hours,
    ROUND(AVG(distinct_content)::numeric, 1)                         AS avg_titles_watched,
    ROUND(AVG(distinct_genres)::numeric, 1)                          AS avg_genres,
    ROUND(SUM(lifetime_revenue_usd), 2)                              AS total_revenue_usd,
    ROUND(AVG(lifetime_revenue_usd), 2)                              AS avg_revenue_usd,
    ROUND(SUM(current_mrr_usd), 2)                                   AS current_mrr_usd,
    -- Revenue concentration: what share of total revenue this segment represents.
    -- The gap between this and pct_of_users is the whole argument for segmentation.
    ROUND(
        100.0 * SUM(lifetime_revenue_usd)
        / NULLIF(SUM(SUM(lifetime_revenue_usd)) OVER (), 0), 2
    )                                                                AS pct_of_revenue,
    COUNT(*) FILTER (WHERE is_premium)::bigint                       AS premium_users,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_premium) / NULLIF(COUNT(*), 0), 1)
                                                                     AS premium_share_pct
FROM segmented
GROUP BY rfm_segment
ORDER BY total_revenue_usd DESC
