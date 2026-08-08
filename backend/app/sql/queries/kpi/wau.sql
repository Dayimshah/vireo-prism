-- Weekly active users on a 7-day rolling basis.
--
-- Rolling, not calendar-week: a calendar WAU jumps every Monday and is unusable
-- for spotting a mid-week change. The window frame counts distinct users across
-- each day and the six before it.
--
-- COUNT(DISTINCT ...) is not permitted in a window function in PostgreSQL, so the
-- distinct-user set per window is built with a LATERAL join over the spine. That is
-- the honest way to do it; the alternative (summing per-day counts) would
-- double-count anyone active on more than one day in the window.
WITH spine AS (
    {{date_spine}}
),
scoped AS (
    SELECT d.user_id, d.activity_date
    FROM analytics.mv_user_daily AS d
    JOIN core.users AS u USING (user_id)
    WHERE d.activity_date BETWEEN (CAST(:date_from AS date) - INTERVAL '6 days') AND CAST(:date_to AS date)
      {{user_filter}}
)
SELECT
    spine.day AS day,
    (
        SELECT COUNT(DISTINCT s.user_id)
        FROM scoped AS s
        WHERE s.activity_date > spine.day - INTERVAL '7 days'
          AND s.activity_date <= spine.day
    )::bigint AS wau
FROM spine
ORDER BY spine.day
