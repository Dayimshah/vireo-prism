-- Signup-month x month-N retention matrix.
--
-- The monthly view of the same triangle as the weekly matrix. Worth having as a
-- separate query rather than a parameter, because monthly cohorts answer a
-- different question: weekly cohorts show onboarding changes, monthly cohorts show
-- whether the business is compounding.
--
-- Month arithmetic uses date_trunc and an interval rather than dividing
-- days_since_signup by 30. That division would drift — a user 60 days in is
-- "month 2" by division but may be in calendar month 1 or 2 depending on which
-- months they spanned, and the drift accumulates across the window.
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
grid AS (
    SELECT s.cohort_month, s.cohort_size, m.month_n
    FROM sizes AS s
    CROSS JOIN generate_series(0, CAST(:max_months AS int)) AS m(month_n)
),
activity AS (
    SELECT
        c.cohort_month,
        -- Whole calendar months between signup month and activity month.
        (EXTRACT(YEAR  FROM date_trunc('month', d.activity_date) - c.cohort_month) * 12
       + EXTRACT(MONTH FROM date_trunc('month', d.activity_date) - c.cohort_month))::int
                                            AS month_n,
        COUNT(DISTINCT d.user_id)::bigint    AS active_users
    FROM cohorts AS c
    JOIN analytics.mv_user_daily AS d ON d.user_id = c.user_id
    WHERE d.activity_date >= c.cohort_month
    GROUP BY c.cohort_month, 2
)
SELECT
    g.cohort_month,
    g.month_n,
    g.cohort_size,
    -- Complete when the whole month has elapsed within the observation window.
    ((g.cohort_month + make_interval(months => g.month_n + 1) - INTERVAL '1 day')::date
        <= CAST(:observation_end AS date)) AS is_complete,
    CASE
        WHEN (g.cohort_month + make_interval(months => g.month_n + 1) - INTERVAL '1 day')::date
             > CAST(:observation_end AS date) THEN NULL
        ELSE COALESCE(a.active_users, 0)
    END AS active_users,
    CASE
        WHEN (g.cohort_month + make_interval(months => g.month_n + 1) - INTERVAL '1 day')::date
             > CAST(:observation_end AS date) THEN NULL
        ELSE ROUND(100.0 * COALESCE(a.active_users, 0) / NULLIF(g.cohort_size, 0), 2)
    END AS retention_pct
FROM grid AS g
LEFT JOIN activity AS a USING (cohort_month, month_n)
ORDER BY g.cohort_month, g.month_n
