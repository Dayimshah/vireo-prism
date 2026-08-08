-- How engagement decays in the weeks after a title joins the catalogue.
--
-- Answers the acquisition question: does a title keep earning, or does it spike on
-- release and go quiet? A steep curve means the catalogue needs constant
-- replenishment; a flat one means library titles carry real weight.
--
-- Only titles added *inside* the observation window are eligible. A back-catalogue
-- title acquired years earlier has no observable launch curve here, and including it
-- would flatten every average with mid-life data misfiled as week 0.
--
-- Indexed to week 0 rather than reported in absolute hours, because absolute hours
-- would rank by title size and hide the shape entirely — one hit would dominate the
-- curve for every genre it belongs to.
WITH eligible AS (
    SELECT
        c.content_id,
        c.title,
        c.added_on,
        c.content_type,
        c.is_original,
        g.name AS genre
    FROM core.content AS c
    JOIN core.genres AS g ON g.genre_id = c.genre_id
    WHERE c.added_on >= CAST(:date_from AS date)
      -- Needs at least eight weeks of runway to show a curve at all.
      AND c.added_on <= (CAST(:date_to AS date) - INTERVAL '56 days')
      {{content_filter}}
),
weekly AS (
    SELECT
        e.content_id,
        e.genre,
        e.content_type,
        e.is_original,
        ((d.activity_date - e.added_on) / 7)::int AS week_since_added,
        SUM(d.watch_seconds)                      AS watch_seconds,
        SUM(d.starts)                             AS starts,
        SUM(d.unique_viewers)                     AS viewer_days
    FROM eligible AS e
    JOIN analytics.mv_content_daily AS d ON d.content_id = e.content_id
    WHERE d.activity_date >= e.added_on
      AND d.activity_date <= CAST(:date_to AS date)
      AND (d.activity_date - e.added_on) < 84   -- twelve weeks
    GROUP BY e.content_id, e.genre, e.content_type, e.is_original, 5
),
with_baseline AS (
    SELECT
        w.*,
        -- Week 0 for this title, used as the index base.
        MAX(w.watch_seconds) FILTER (WHERE w.week_since_added = 0)
            OVER (PARTITION BY w.content_id) AS week0_watch_seconds
    FROM weekly AS w
)
SELECT
    week_since_added,
    COUNT(DISTINCT content_id)::bigint                                AS titles,
    ROUND(SUM(watch_seconds)::numeric / 3600.0, 1)                    AS watch_hours,
    SUM(starts)::bigint                                               AS starts,
    -- Mean of per-title indices, not an index of the totals. Averaging the ratios
    -- gives every title equal weight, so the curve describes a typical title rather
    -- than the biggest one.
    ROUND(
        100.0 * AVG(
            watch_seconds::numeric / NULLIF(week0_watch_seconds, 0)
        ) FILTER (WHERE week0_watch_seconds > 0), 1
    )                                                                 AS pct_of_week0_mean,
    ROUND(
        100.0 * PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY watch_seconds::numeric / NULLIF(week0_watch_seconds, 0)
        )::numeric, 1
    )                                                                 AS pct_of_week0_median,
    ROUND(
        100.0 * AVG(watch_seconds::numeric / NULLIF(week0_watch_seconds, 0))
                FILTER (WHERE is_original AND week0_watch_seconds > 0), 1
    )                                                                 AS pct_of_week0_originals,
    ROUND(
        100.0 * AVG(watch_seconds::numeric / NULLIF(week0_watch_seconds, 0))
                FILTER (WHERE NOT is_original AND week0_watch_seconds > 0), 1
    )                                                                 AS pct_of_week0_licensed
FROM with_baseline
GROUP BY week_since_added
ORDER BY week_since_added
