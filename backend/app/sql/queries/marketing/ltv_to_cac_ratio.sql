-- LTV:CAC quadrant classification, shaped for a scatter chart.
--
-- Same inputs as cohort_ltv_by_channel, different output: this returns the two axes
-- plus a quadrant label, so the Marketing page can plot spend against return and the
-- reader sees which channels to scale without interpreting a table.
--
-- The quadrant boundaries use the *population medians* rather than fixed thresholds.
-- A hard-coded "LTV:CAC above 3 is good" rule is industry folklore that ignores
-- margin structure; splitting on the observed median makes the comparison relative to
-- this business, which is the only comparison that is actually available.
--
-- Organic channels are labelled separately rather than being given an infinite ratio.
-- Zero CAC is not an achievement to rank, and including them would compress every
-- paid channel into the left edge of the chart.
WITH cohort AS (
    SELECT
        u.user_id,
        ch.name AS channel,
        ch.channel_group,
        ch.is_paid,
        ch.cac_usd
    FROM core.users AS u
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
per_channel AS (
    SELECT
        c.channel,
        c.channel_group,
        c.is_paid,
        c.cac_usd,
        COUNT(*)                                        AS users_acquired,
        COUNT(l.user_id) FILTER (WHERE l.lifetime_revenue_usd > 0) AS converted,
        SUM(COALESCE(l.lifetime_revenue_usd, 0))        AS revenue_usd,
        SUM(COALESCE(l.lifetime_revenue_usd, 0)) / NULLIF(COUNT(*), 0) AS ltv_per_user,
        AVG(COALESCE(l.total_watch_seconds, 0))         AS avg_watch_seconds,
        AVG(l.completion_rate)                          AS avg_completion_rate
    FROM cohort AS c
    LEFT JOIN analytics.mv_user_lifetime AS l USING (user_id)
    GROUP BY c.channel, c.channel_group, c.is_paid, c.cac_usd
    HAVING COUNT(*) >= CAST(:min_cohort_size AS int)
),
medians AS (
    -- Computed over paid channels only: including zero-CAC channels would drag the
    -- CAC median toward zero and misclassify every paid channel as expensive.
    --
    -- PERCENTILE_CONT returns double precision even for numeric input, and
    -- round(double precision, int) does not exist in PostgreSQL. Casting here rather
    -- than at each use site keeps the arithmetic below in one numeric domain.
    SELECT
        (PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ltv_per_user))::numeric AS median_ltv,
        (PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cac_usd))::numeric      AS median_cac
    FROM per_channel
    WHERE is_paid AND cac_usd > 0
)
SELECT
    p.channel,
    p.channel_group,
    p.is_paid,
    p.users_acquired::bigint,
    p.converted::bigint,
    ROUND(100.0 * p.converted / NULLIF(p.users_acquired, 0), 2)      AS conversion_pct,
    ROUND(p.cac_usd, 2)                                             AS cac_usd,
    ROUND(p.ltv_per_user, 2)                                        AS ltv_per_user_usd,
    ROUND(p.revenue_usd, 2)                                         AS total_revenue_usd,
    ROUND(p.cac_usd * p.users_acquired, 2)                          AS total_spend_usd,
    CASE
        WHEN p.cac_usd = 0 THEN NULL
        ELSE ROUND(p.ltv_per_user / p.cac_usd, 2)
    END                                                             AS ltv_to_cac_ratio,
    ROUND(p.avg_watch_seconds::numeric / 3600.0, 1)                 AS avg_watch_hours,
    ROUND(p.avg_completion_rate::numeric, 3)                        AS avg_completion_rate,
    ROUND(m.median_ltv, 2)                                          AS median_ltv_usd,
    ROUND(m.median_cac, 2)                                          AS median_cac_usd,
    CASE
        WHEN NOT p.is_paid OR p.cac_usd = 0             THEN 'organic'
        WHEN p.ltv_per_user >= m.median_ltv
         AND p.cac_usd <  m.median_cac                 THEN 'scale up'
        WHEN p.ltv_per_user >= m.median_ltv
         AND p.cac_usd >= m.median_cac                 THEN 'efficient but expensive'
        WHEN p.ltv_per_user <  m.median_ltv
         AND p.cac_usd <  m.median_cac                 THEN 'cheap but weak'
        ELSE 'cut or fix'
    END                                                             AS quadrant,
    -- Whether the channel has recovered its cost at all, independent of quadrant.
    (p.revenue_usd >= p.cac_usd * p.users_acquired)                 AS is_profitable
FROM per_channel AS p
CROSS JOIN medians AS m
ORDER BY ltv_to_cac_ratio DESC NULLS LAST
