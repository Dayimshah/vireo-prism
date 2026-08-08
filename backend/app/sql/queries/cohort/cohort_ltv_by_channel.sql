-- Lifetime value per acquisition channel, with CAC and the resulting ratio.
--
-- The headline marketing finding, and the one the whole simulation was arranged to
-- make discoverable. Nothing here reads the generator's coefficients: revenue is
-- recognised from core.subscriptions, CAC comes from the channel dimension, and the
-- ratio falls out. If Referral beats Display, that was recovered.
--
-- LTV is measured per *acquired user*, not per paying user. Dividing by payers only
-- would flatter every paid channel, because it silently discards the users the
-- channel brought who never converted — which is precisely the cost being measured.
WITH cohort AS (
    SELECT
        u.user_id,
        u.signup_date,
        ch.channel_id,
        ch.name          AS channel,
        ch.channel_group,
        ch.is_paid,
        ch.cac_usd
    FROM core.users AS u
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
revenue AS (
    -- Monthly recognition across each subscription's life, capped at the
    -- observation end so no unearned future revenue is counted.
    SELECT
        c.user_id,
        SUM(s.mrr_usd) AS realised_revenue_usd
    FROM cohort AS c
    JOIN core.subscriptions AS s ON s.user_id = c.user_id
    CROSS JOIN LATERAL generate_series(
        date_trunc('month', s.started_on),
        date_trunc('month', LEAST(COALESCE(s.ended_on, CAST(:observation_end AS date)),
                                  CAST(:observation_end AS date))),
        INTERVAL '1 month'
    ) AS month_start
    WHERE s.mrr_usd > 0
    GROUP BY c.user_id
)
SELECT
    c.channel,
    c.channel_group,
    c.is_paid,
    COUNT(*)::bigint                                              AS users_acquired,
    COUNT(r.user_id)::bigint                                      AS users_converted,
    ROUND(100.0 * COUNT(r.user_id) / NULLIF(COUNT(*), 0), 2)      AS conversion_pct,
    ROUND(c.cac_usd, 2)                                           AS cac_usd,
    ROUND(COALESCE(SUM(r.realised_revenue_usd), 0), 2)            AS total_revenue_usd,
    -- LTV across everyone acquired: the figure comparable to CAC.
    ROUND(COALESCE(SUM(r.realised_revenue_usd), 0) / NULLIF(COUNT(*), 0), 2)
                                                                  AS ltv_per_acquired_usd,
    -- ARPPU: revenue per paying user. Higher by construction; useful for pricing,
    -- misleading for channel comparison.
    ROUND(COALESCE(SUM(r.realised_revenue_usd), 0) / NULLIF(COUNT(r.user_id), 0), 2)
                                                                  AS revenue_per_payer_usd,
    -- Organic channels have zero CAC, so the ratio is undefined rather than
    -- infinite. NULL is the honest answer; a sentinel would be plotted.
    CASE
        WHEN c.cac_usd = 0 THEN NULL
        ELSE ROUND(
            (COALESCE(SUM(r.realised_revenue_usd), 0) / NULLIF(COUNT(*), 0)) / c.cac_usd,
            2
        )
    END                                                           AS ltv_to_cac_ratio,
    ROUND(c.cac_usd * COUNT(*), 2)                                AS total_spend_usd,
    ROUND(COALESCE(SUM(r.realised_revenue_usd), 0) - c.cac_usd * COUNT(*), 2)
                                                                  AS net_contribution_usd
FROM cohort AS c
LEFT JOIN revenue AS r USING (user_id)
GROUP BY c.channel, c.channel_group, c.is_paid, c.cac_usd
HAVING COUNT(*) >= CAST(:min_cohort_size AS int)
ORDER BY ltv_to_cac_ratio DESC NULLS LAST, ltv_per_acquired_usd DESC
