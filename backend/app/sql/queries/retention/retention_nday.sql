-- Classic day-N retention: was the user active on exactly day N after signup.
--
-- The strictest of the three retention definitions, and the one most often quoted
-- without saying which it is. "Day 7 retention" here means active on day 7
-- precisely — not day 7 or later, and not within the first 7 days.
--
-- The critical correctness detail is the cohort denominator. A user who signed up
-- three days ago cannot possibly be day-7 retained, so including them would report
-- a collapsing retention curve that is really just an incomplete observation
-- window. `eligible` filters each cohort to users who have had the chance.
WITH cohorts AS (
    SELECT
        u.user_id,
        u.signup_date
    FROM core.users AS u
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
milestones AS (
    SELECT unnest(ARRAY[1, 3, 7, 14, 28, 60, 90]) AS day_n
),
eligible AS (
    -- Cohort size per milestone, counting only users old enough to be measured.
    SELECT
        m.day_n,
        COUNT(*)::bigint AS cohort_size
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
     AND d.days_since_signup = m.day_n
    WHERE c.signup_date + m.day_n <= CAST(:observation_end AS date)
    GROUP BY m.day_n
)
SELECT
    e.day_n                                                          AS day_n,
    e.cohort_size                                                    AS cohort_size,
    COALESCE(r.retained_users, 0)                                    AS retained_users,
    ROUND(100.0 * COALESCE(r.retained_users, 0) / NULLIF(e.cohort_size, 0), 2)
                                                                     AS retention_pct
FROM eligible AS e
LEFT JOIN retained AS r USING (day_n)
ORDER BY e.day_n
