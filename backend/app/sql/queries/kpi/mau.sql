-- Monthly active users on a 28-day rolling basis.
--
-- 28 days rather than a calendar month, so every point covers exactly four weeks
-- and the series is not distorted by February being short or by a month containing
-- five weekends instead of four. Calendar-month MAU is a reporting convention;
-- rolling MAU is what you look at to see whether the product is growing.
WITH spine AS (
    {{date_spine}}
),
scoped AS (
    SELECT d.user_id, d.activity_date
    FROM analytics.mv_user_daily AS d
    JOIN core.users AS u USING (user_id)
    WHERE d.activity_date BETWEEN (CAST(:date_from AS date) - INTERVAL '27 days') AND CAST(:date_to AS date)
      {{user_filter}}
)
SELECT
    spine.day AS day,
    (
        SELECT COUNT(DISTINCT s.user_id)
        FROM scoped AS s
        WHERE s.activity_date > spine.day - INTERVAL '28 days'
          AND s.activity_date <= spine.day
    )::bigint AS mau
FROM spine
ORDER BY spine.day
