-- Daily active users over the requested window.
--
-- Reads analytics.mv_user_daily rather than core.events: the view is already one
-- row per user per active day, so DAU is a COUNT over ~900k narrow rows instead of
-- a COUNT DISTINCT over millions of event rows.
--
-- The date spine is not decoration. A LEFT JOIN puts an explicit 0 on a day with
-- no activity, so an outage or a data gap shows as a dip. Without it the chart
-- would join the two neighbouring points and imply continuity that never existed.
WITH spine AS (
    {{date_spine}}
),
active AS (
    SELECT
        d.activity_date,
        COUNT(DISTINCT d.user_id) AS active_users,
        SUM(d.sessions)           AS sessions,
        SUM(d.watch_seconds)      AS watch_seconds
    FROM analytics.mv_user_daily AS d
    JOIN core.users AS u USING (user_id)
    WHERE d.activity_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
    GROUP BY d.activity_date
)
SELECT
    spine.day                                   AS day,
    COALESCE(active.active_users, 0)::bigint    AS dau,
    COALESCE(active.sessions, 0)::bigint        AS sessions,
    COALESCE(active.watch_seconds, 0)::bigint   AS watch_seconds,
    ROUND(
        COALESCE(active.watch_seconds, 0)::numeric
        / NULLIF(active.active_users, 0) / 60.0,
        1
    )                                           AS watch_minutes_per_user
FROM spine
LEFT JOIN active ON active.activity_date = spine.day
ORDER BY spine.day
