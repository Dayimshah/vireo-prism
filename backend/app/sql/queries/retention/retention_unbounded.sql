-- Unbounded retention: active at any point within the first N days.
--
-- The loosest of the three definitions, and the one that answers a different
-- question from the other two: not "are they still here" but "did they ever engage
-- at all". Day-1 unbounded retention is effectively an activation metric — a user
-- who signed up and never returned shows 100% at day 1 and flat thereafter.
--
-- Read alongside retention_nday and retention_rolling, the three curves together
-- separate three distinct failure modes: never activated (low unbounded), irregular
-- cadence (wide gap between classic and rolling), and genuine churn (all three
-- decaying together).
WITH cohorts AS (
    SELECT u.user_id, u.signup_date
    FROM core.users AS u
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
milestones AS (
    SELECT unnest(ARRAY[1, 3, 7, 14, 28, 60, 90]) AS day_n
),
eligible AS (
    SELECT m.day_n, COUNT(*)::bigint AS cohort_size
    FROM cohorts AS c
    CROSS JOIN milestones AS m
    WHERE c.signup_date + m.day_n <= CAST(:observation_end AS date)
    GROUP BY m.day_n
),
retained AS (
    SELECT
        m.day_n,
        COUNT(DISTINCT d.user_id)::bigint AS retained_users
    FROM cohorts AS c
    CROSS JOIN milestones AS m
    JOIN analytics.mv_user_daily AS d
      ON d.user_id = c.user_id
     -- The defining difference: any activity in [1, N], not activity *on* N.
     -- Day 0 is excluded so signup day alone does not count as retention.
     AND d.days_since_signup BETWEEN 1 AND m.day_n
    WHERE c.signup_date + m.day_n <= CAST(:observation_end AS date)
    GROUP BY m.day_n
)
SELECT
    e.day_n,
    e.cohort_size,
    COALESCE(r.retained_users, 0)                                     AS retained_users,
    ROUND(100.0 * COALESCE(r.retained_users, 0) / NULLIF(e.cohort_size, 0), 2)
                                                                      AS retention_pct
FROM eligible AS e
LEFT JOIN retained AS r USING (day_n)
ORDER BY e.day_n
