-- Stickiness: DAU as a percentage of MAU.
--
-- The single most informative engagement ratio, because it is scale-free. It reads
-- as "on how many days per month does a typical active user show up" — 30% means
-- roughly 9 days out of 28. Growth in absolute DAU can come purely from
-- acquisition; stickiness cannot, so it exposes whether the product is actually
-- becoming more habitual.
--
-- Anything above ~50% is exceptional for streaming and would suggest a bug in the
-- calculation rather than a triumph.
--
-- Performance note. The natural phrasing puts two correlated subqueries in the
-- SELECT list, one for DAU and one for the 28-day MAU window, evaluated per row of
-- the date spine. Over 548 days that is 1,096 separate scans of the activity set and
-- measured 6.2s.
--
-- The rewrite below inverts the problem. Instead of asking each day "who was active
-- in your window", each user-day is fanned out across the 28 days it contributes MAU
-- to, and the counts fall out of a single grouped pass. That is O(28n) rather than
-- O(n·days), and the fan-out is cheap because mv_user_daily is already narrow.
WITH spine AS (
    {{date_spine}}
),
scoped AS (
    -- Already unique on (user_id, activity_date) — mv_user_daily carries a unique
    -- index on that pair — so no DISTINCT is needed here.
    --
    -- Reaches 27 days before date_from so the earliest requested day has a complete
    -- trailing window rather than a truncated one that would understate MAU.
    SELECT d.user_id, d.activity_date
    FROM analytics.mv_user_daily AS d
    JOIN core.users AS u USING (user_id)
    WHERE d.activity_date BETWEEN (CAST(:date_from AS date) - 27) AND CAST(:date_to AS date)
      {{user_filter}}
),
dau AS (
    SELECT activity_date AS day, COUNT(*)::bigint AS dau
    FROM scoped
    GROUP BY activity_date
),
mau AS (
    -- One row per (user, day-they-count-toward). A user active on the 3rd is part of
    -- the trailing-28 window for the 3rd through the 30th.
    SELECT
        window_day::date       AS day,
        COUNT(DISTINCT s.user_id)::bigint AS mau
    FROM scoped AS s
    CROSS JOIN LATERAL generate_series(
        s.activity_date::timestamp,
        (s.activity_date + 27)::timestamp,
        INTERVAL '1 day'
    ) AS window_day
    GROUP BY window_day::date
)
SELECT
    spine.day                                   AS day,
    COALESCE(dau.dau, 0)                        AS dau,
    COALESCE(mau.mau, 0)                        AS mau,
    ROUND(100.0 * dau.dau / NULLIF(mau.mau, 0), 2) AS stickiness_pct
FROM spine
LEFT JOIN dau ON dau.day = spine.day
LEFT JOIN mau ON mau.day = spine.day
ORDER BY spine.day
