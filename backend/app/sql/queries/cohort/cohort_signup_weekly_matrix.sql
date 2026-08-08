-- Signup-week x week-N retention matrix. The classic cohort triangle.
--
-- Returned in long form (one row per cohort-week cell) rather than pivoted into
-- columns. Two reasons: PostgreSQL cannot produce a variable number of columns from
-- a single query without crosstab gymnastics, and the frontend heatmap wants long
-- data anyway.
--
-- The `is_complete` flag is the important column and the one most cohort analyses
-- omit. A cell where the cohort has not yet lived long enough to reach week N is
-- structurally different from a cell where they had the chance and did not return.
-- Reporting both as "0%" produces the diagonal cliff of zeros that makes so many
-- cohort charts wrong. Here incomplete cells return NULL and are flagged, so the
-- dashboard can grey them out.
WITH cohorts AS (
    SELECT
        u.user_id,
        date_trunc('week', u.signup_date)::date AS cohort_week
    FROM core.users AS u
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
sizes AS (
    SELECT cohort_week, COUNT(*)::bigint AS cohort_size
    FROM cohorts
    GROUP BY cohort_week
    HAVING COUNT(*) >= CAST(:min_cohort_size AS int)
),
grid AS (
    SELECT s.cohort_week, s.cohort_size, w.week_n
    FROM sizes AS s
    CROSS JOIN generate_series(0, CAST(:max_weeks AS int)) AS w(week_n)
),
activity AS (
    SELECT
        c.cohort_week,
        (d.days_since_signup / 7)          AS week_n,
        COUNT(DISTINCT d.user_id)::bigint   AS active_users
    FROM cohorts AS c
    JOIN analytics.mv_user_daily AS d ON d.user_id = c.user_id
    WHERE d.days_since_signup >= 0
    GROUP BY c.cohort_week, (d.days_since_signup / 7)
)
SELECT
    g.cohort_week,
    g.week_n,
    g.cohort_size,
    -- Cell is complete only if the whole week elapsed before the observation end.
    (g.cohort_week + (g.week_n * 7 + 6) <= CAST(:observation_end AS date)) AS is_complete,
    CASE
        WHEN g.cohort_week + (g.week_n * 7 + 6) > CAST(:observation_end AS date) THEN NULL
        ELSE COALESCE(a.active_users, 0)
    END AS active_users,
    CASE
        WHEN g.cohort_week + (g.week_n * 7 + 6) > CAST(:observation_end AS date) THEN NULL
        ELSE ROUND(100.0 * COALESCE(a.active_users, 0) / NULLIF(g.cohort_size, 0), 2)
    END AS retention_pct
FROM grid AS g
LEFT JOIN activity AS a USING (cohort_week, week_n)
ORDER BY g.cohort_week, g.week_n
