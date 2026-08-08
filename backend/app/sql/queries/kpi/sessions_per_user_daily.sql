-- Sessions per active user per day, with the distribution around the mean.
--
-- The mean alone is misleading here: session counts are right-skewed, so a handful
-- of heavy users pull it above what a typical user does. Reporting the median and
-- p90 alongside it makes the skew visible, which is the difference between "our
-- users average 3 sessions a day" and the truth that most have 1 and a few have 12.
WITH spine AS (
    {{date_spine}}
),
per_user AS (
    SELECT
        d.activity_date,
        d.user_id,
        d.sessions,
        d.events
    FROM analytics.mv_user_daily AS d
    JOIN core.users AS u USING (user_id)
    WHERE d.activity_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
daily AS (
    SELECT
        activity_date,
        COUNT(*)                                                        AS active_users,
        SUM(sessions)                                                   AS total_sessions,
        ROUND(AVG(sessions)::numeric, 3)                                AS mean_sessions,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY sessions)           AS median_sessions,
        PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY sessions)           AS p90_sessions,
        ROUND(AVG(events)::numeric, 2)                                  AS mean_events
    FROM per_user
    GROUP BY activity_date
)
SELECT
    spine.day                                        AS day,
    COALESCE(daily.active_users, 0)::bigint          AS active_users,
    COALESCE(daily.total_sessions, 0)::bigint        AS total_sessions,
    COALESCE(daily.mean_sessions, 0)                 AS mean_sessions_per_user,
    COALESCE(daily.median_sessions, 0)               AS median_sessions_per_user,
    COALESCE(daily.p90_sessions, 0)                  AS p90_sessions_per_user,
    COALESCE(daily.mean_events, 0)                   AS mean_events_per_user
FROM spine
LEFT JOIN daily ON daily.activity_date = spine.day
ORDER BY spine.day
