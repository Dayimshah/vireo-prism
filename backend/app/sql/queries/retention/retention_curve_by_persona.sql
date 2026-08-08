-- Weekly retention curve for each persona, weeks 0 through 12.
--
-- This is the chart that proves the dataset has causal structure. The persona
-- coefficients live in core.personas and in the seeder; this query reads neither.
-- It counts activity in the event stream and groups by a foreign key. If Binge
-- Watchers retain better than Churn Risk here, that ordering was recovered, not
-- asserted.
--
-- Weekly rather than daily buckets, because a daily curve at persona granularity
-- is mostly sampling noise: dividing each cohort by seven flattens the weekend
-- effect that would otherwise make every curve oscillate.
WITH cohorts AS (
    SELECT
        u.user_id,
        u.signup_date,
        p.name AS persona
    FROM core.users AS u
    JOIN core.personas AS p ON p.persona_id = u.persona_id
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
weeks AS (
    SELECT generate_series(0, 12) AS week_n
),
eligible AS (
    SELECT
        c.persona,
        w.week_n,
        COUNT(*)::bigint AS cohort_size
    FROM cohorts AS c
    CROSS JOIN weeks AS w
    -- Only users observable through the end of that week are in its denominator.
    WHERE c.signup_date + (w.week_n * 7 + 6) <= CAST(:observation_end AS date)
    GROUP BY c.persona, w.week_n
),
retained AS (
    SELECT
        c.persona,
        (d.days_since_signup / 7) AS week_n,
        COUNT(DISTINCT d.user_id)::bigint AS retained_users
    FROM cohorts AS c
    JOIN analytics.mv_user_daily AS d ON d.user_id = c.user_id
    WHERE d.days_since_signup BETWEEN 0 AND 90
      AND c.signup_date + ((d.days_since_signup / 7) * 7 + 6) <= CAST(:observation_end AS date)
    GROUP BY c.persona, (d.days_since_signup / 7)
)
SELECT
    e.persona,
    e.week_n,
    e.cohort_size,
    COALESCE(r.retained_users, 0)                                     AS retained_users,
    ROUND(100.0 * COALESCE(r.retained_users, 0) / NULLIF(e.cohort_size, 0), 2)
                                                                      AS retention_pct
FROM eligible AS e
LEFT JOIN retained AS r USING (persona, week_n)
WHERE e.cohort_size >= CAST(:min_cohort_size AS int)
ORDER BY e.persona, e.week_n
