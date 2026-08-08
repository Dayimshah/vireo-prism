-- Months until a cohort's cumulative revenue covers its acquisition cost.
--
-- The number that decides whether a paid channel can be scaled. A channel with a
-- healthy LTV:CAC ratio but a 20-month payback still consumes cash faster than it
-- returns it, which is how companies grow themselves into insolvency.
--
-- Revenue is recognised month by month across each subscription's life rather than
-- booked at its start, because payback is inherently a question about timing. Booking
-- it up front would show every channel paying back in month zero.
--
-- Organic channels have zero CAC and therefore no payback period. NULL is returned
-- rather than 0 — "already paid back" and "never had a cost" are different facts, and
-- collapsing them would put organic at the top of a ranking it does not belong in.
WITH cohort AS (
    SELECT
        u.user_id,
        ch.name       AS channel,
        ch.channel_group,
        ch.is_paid,
        ch.cac_usd,
        date_trunc('month', u.signup_date)::date AS cohort_month
    FROM core.users AS u
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
channel_totals AS (
    SELECT
        channel, channel_group, is_paid, cac_usd,
        COUNT(*)                      AS users_acquired,
        SUM(cac_usd)                  AS total_spend_usd
    FROM cohort
    GROUP BY channel, channel_group, is_paid, cac_usd
),
monthly_revenue AS (
    -- One row per subscription per month it was active, attributed to the channel
    -- that acquired the user.
    SELECT
        c.channel,
        -- Whole months between the two month starts, computed arithmetically.
        -- Subtracting dates directly would give an integer day count, which
        -- EXTRACT cannot consume.
        ((EXTRACT(YEAR FROM month_start)::int * 12
          + EXTRACT(MONTH FROM month_start)::int)
       - (EXTRACT(YEAR FROM c.cohort_month)::int * 12
          + EXTRACT(MONTH FROM c.cohort_month)::int))::int
                                      AS month_n,
        SUM(s.mrr_usd)                AS revenue_usd
    FROM cohort AS c
    JOIN core.subscriptions AS s ON s.user_id = c.user_id
    CROSS JOIN LATERAL generate_series(
        date_trunc('month', s.started_on),
        date_trunc('month', LEAST(COALESCE(s.ended_on, CAST(:observation_end AS date)),
                                  CAST(:observation_end AS date))),
        INTERVAL '1 month'
    ) AS month_start
    WHERE s.mrr_usd > 0
    GROUP BY c.channel, 2
),
cumulative AS (
    SELECT
        r.channel,
        r.month_n,
        r.revenue_usd,
        SUM(r.revenue_usd) OVER (
            PARTITION BY r.channel ORDER BY r.month_n
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cumulative_revenue_usd
    FROM monthly_revenue AS r
    WHERE r.month_n >= 0
),
payback AS (
    -- Earliest month where cumulative revenue clears total spend.
    SELECT
        c.channel,
        MIN(c.month_n) FILTER (
            WHERE c.cumulative_revenue_usd >= t.total_spend_usd
        ) AS payback_month
    FROM cumulative AS c
    JOIN channel_totals AS t USING (channel)
    GROUP BY c.channel
)
SELECT
    t.channel,
    t.channel_group,
    t.is_paid,
    t.users_acquired::bigint,
    ROUND(t.cac_usd, 2)                                              AS cac_per_user_usd,
    ROUND(t.total_spend_usd, 2)                                      AS total_spend_usd,
    ROUND(COALESCE(MAX(c.cumulative_revenue_usd), 0), 2)             AS revenue_to_date_usd,
    ROUND(
        COALESCE(MAX(c.cumulative_revenue_usd), 0) - t.total_spend_usd, 2
    )                                                                AS net_position_usd,
    CASE WHEN t.cac_usd = 0 THEN NULL ELSE p.payback_month END       AS payback_months,
    CASE
        WHEN t.cac_usd = 0                THEN 'no acquisition cost'
        WHEN p.payback_month IS NULL      THEN 'not yet recovered'
        WHEN p.payback_month <= 3         THEN 'fast (<= 3 months)'
        WHEN p.payback_month <= 9         THEN 'acceptable (<= 9 months)'
        WHEN p.payback_month <= 18        THEN 'slow (<= 18 months)'
        ELSE 'very slow (> 18 months)'
    END                                                              AS payback_band,
    ROUND(
        COALESCE(MAX(c.cumulative_revenue_usd), 0) / NULLIF(t.users_acquired, 0), 2
    )                                                                AS revenue_per_user_usd,
    CASE
        WHEN t.cac_usd = 0 THEN NULL
        ELSE ROUND(
            (COALESCE(MAX(c.cumulative_revenue_usd), 0) / NULLIF(t.users_acquired, 0))
            / t.cac_usd, 2
        )
    END                                                              AS ltv_to_cac_ratio
FROM channel_totals AS t
LEFT JOIN cumulative AS c USING (channel)
LEFT JOIN payback    AS p USING (channel)
GROUP BY t.channel, t.channel_group, t.is_paid, t.users_acquired,
         t.cac_usd, t.total_spend_usd, p.payback_month
HAVING t.users_acquired >= CAST(:min_cohort_size AS int)
ORDER BY t.is_paid DESC, payback_months NULLS LAST
