-- Daily split of new, returning and resurrected active users.
--
-- Three-way rather than two-way, and the third bucket is the point. A user who was
-- inactive for over 28 days and came back is not "returning" in any useful sense —
-- they are a win-back, and mixing them into the returning line hides both the
-- churn that preceded them and the recovery itself.
--
--   new         first ever activity was today
--   returning   active today and within the preceding 28 days
--   resurrected active today, but dormant for more than 28 days
WITH spine AS (
    {{date_spine}}
),
scoped AS (
    SELECT
        d.user_id,
        d.activity_date,
        d.days_since_signup,
        -- Previous active day for this user, from which dormancy is measured.
        LAG(d.activity_date) OVER (
            PARTITION BY d.user_id ORDER BY d.activity_date
        ) AS previous_active_date
    FROM analytics.mv_user_daily AS d
    JOIN core.users AS u USING (user_id)
    WHERE d.activity_date <= CAST(:date_to AS date)
      {{user_filter}}
),
classified AS (
    SELECT
        activity_date,
        CASE
            WHEN previous_active_date IS NULL THEN 'new'
            WHEN activity_date - previous_active_date > 28 THEN 'resurrected'
            ELSE 'returning'
        END AS bucket
    FROM scoped
    WHERE activity_date >= CAST(:date_from AS date)
)
SELECT
    spine.day                                                        AS day,
    COUNT(*) FILTER (WHERE c.bucket = 'new')::bigint                 AS new_users,
    COUNT(*) FILTER (WHERE c.bucket = 'returning')::bigint           AS returning_users,
    COUNT(*) FILTER (WHERE c.bucket = 'resurrected')::bigint         AS resurrected_users,
    COUNT(c.bucket)::bigint                                          AS total_active
FROM spine
LEFT JOIN classified AS c ON c.activity_date = spine.day
GROUP BY spine.day
ORDER BY spine.day
