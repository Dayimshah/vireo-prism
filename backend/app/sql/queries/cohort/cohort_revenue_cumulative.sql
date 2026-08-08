-- Cumulative revenue per signup cohort, by months since signup.
--
-- The chart that answers "how long until a cohort pays for itself", and the input
-- to CAC payback. Revenue is recognised monthly across each subscription's life
-- rather than booked entirely at its start date — booking it up front would show
-- cohorts breaking even instantly and make payback analysis meaningless.
--
-- generate_series over each subscription's active months is the honest way to
-- spread it. An open subscription is recognised only up to the observation end, so
-- no future revenue is counted.
WITH cohorts AS (
    SELECT
        u.user_id,
        date_trunc('month', u.signup_date)::date AS cohort_month
    FROM core.users AS u
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
sizes AS (
    SELECT cohort_month, COUNT(*)::bigint AS cohort_size
    FROM cohorts
    GROUP BY cohort_month
    HAVING COUNT(*) >= CAST(:min_cohort_size AS int)
),
-- One row per subscription per month it was active.
recognised AS (
    SELECT
        c.cohort_month,
        date_trunc('month', month_start)::date AS revenue_month,
        s.mrr_usd
    FROM core.subscriptions AS s
    JOIN cohorts AS c ON c.user_id = s.user_id
    CROSS JOIN LATERAL generate_series(
        date_trunc('month', s.started_on),
        date_trunc('month', LEAST(COALESCE(s.ended_on, CAST(:observation_end AS date)),
                                  CAST(:observation_end AS date))),
        INTERVAL '1 month'
    ) AS month_start
    WHERE s.mrr_usd > 0
),
by_month AS (
    SELECT
        r.cohort_month,
        -- date - date yields an integer (days), not an interval, so EXTRACT cannot
        -- be used on it. Whole months are computed arithmetically from the two
        -- month-truncated dates instead.
        ((EXTRACT(YEAR FROM r.revenue_month)::int * 12
          + EXTRACT(MONTH FROM r.revenue_month)::int)
       - (EXTRACT(YEAR FROM r.cohort_month)::int * 12
          + EXTRACT(MONTH FROM r.cohort_month)::int))::int AS month_n,
        SUM(r.mrr_usd) AS revenue_usd
    FROM recognised AS r
    GROUP BY r.cohort_month, 2
)
SELECT
    s.cohort_month,
    b.month_n,
    s.cohort_size,
    ROUND(b.revenue_usd, 2)                                          AS revenue_usd,
    ROUND(
        SUM(b.revenue_usd) OVER (
            PARTITION BY s.cohort_month ORDER BY b.month_n
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ), 2
    )                                                                AS cumulative_revenue_usd,
    -- Per-user cumulative revenue: the figure that gets compared against CAC.
    ROUND(
        SUM(b.revenue_usd) OVER (
            PARTITION BY s.cohort_month ORDER BY b.month_n
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) / NULLIF(s.cohort_size, 0), 2
    )                                                                AS cumulative_arpu_usd
FROM by_month AS b
JOIN sizes AS s USING (cohort_month)
WHERE b.month_n >= 0
ORDER BY s.cohort_month, b.month_n
