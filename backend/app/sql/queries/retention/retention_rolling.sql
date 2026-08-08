-- Rolling retention: active on day N *or any day after*.
--
-- The most forgiving of the three definitions, and the most useful for judging
-- whether a user is genuinely lost. Classic day-7 retention counts a user who
-- happened to skip day 7 as churned even if they returned on day 8; rolling
-- retention does not.
--
-- The gap between this curve and the classic one is itself informative: a wide gap
-- means users are engaged but on an irregular cadence, which calls for a different
-- intervention than users who simply leave.
--
-- Implemented against each user's maximum days_since_signup, which answers "did
-- they ever come back at or after day N" in a single pass.
WITH cohorts AS (
    SELECT
        u.user_id,
        u.signup_date
    FROM core.users AS u
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
last_seen AS (
    SELECT
        c.user_id,
        c.signup_date,
        MAX(d.days_since_signup) AS last_active_day
    FROM cohorts AS c
    LEFT JOIN analytics.mv_user_daily AS d ON d.user_id = c.user_id
    GROUP BY c.user_id, c.signup_date
),
milestones AS (
    SELECT unnest(ARRAY[1, 3, 7, 14, 28, 60, 90]) AS day_n
)
SELECT
    m.day_n                                                          AS day_n,
    COUNT(*)::bigint                                                 AS cohort_size,
    COUNT(*) FILTER (WHERE l.last_active_day >= m.day_n)::bigint     AS retained_users,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE l.last_active_day >= m.day_n)
        / NULLIF(COUNT(*), 0),
        2
    )                                                                AS retention_pct
FROM last_seen AS l
CROSS JOIN milestones AS m
-- Same eligibility rule as classic retention: only users who could have reached
-- the milestone are in its denominator.
WHERE l.signup_date + m.day_n <= CAST(:observation_end AS date)
GROUP BY m.day_n
ORDER BY m.day_n
