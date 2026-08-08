-- Genre performance across every dimension that matters for commissioning.
--
-- One row per genre, so the question "what should we buy more of" has a single table
-- behind it. The columns are deliberately in tension: catalogue_share against
-- watch_share exposes over- and under-invested genres, and the ratio between them is
-- the efficiency column.
--
-- A genre holding 12% of the catalogue but driving 4% of watch time is
-- over-commissioned; the reverse is an opportunity. That comparison is the entire
-- point of the query and it is invisible from either column alone.
WITH catalogue AS (
    SELECT
        g.genre_id,
        g.name                                          AS genre,
        COUNT(*)                                        AS titles,
        COUNT(*) FILTER (WHERE c.is_original)           AS originals,
        ROUND(AVG(c.runtime_minutes)::numeric, 0)       AS avg_runtime_minutes,
        ROUND(AVG(c.popularity_score)::numeric, 1)      AS avg_popularity,
        COUNT(*) FILTER (WHERE c.content_type = 'series') AS series_count
    FROM core.content AS c
    JOIN core.genres  AS g ON g.genre_id = c.genre_id
    WHERE TRUE
      {{content_filter}}
    GROUP BY g.genre_id, g.name
),
engagement AS (
    SELECT
        c.genre_id,
        SUM(d.watch_seconds)                            AS watch_seconds,
        SUM(d.starts)                                   AS starts,
        SUM(d.completions)                              AS completions,
        SUM(d.detail_views)                             AS detail_views,
        SUM(d.trailer_views)                            AS trailer_views,
        SUM(d.watchlist_adds)                           AS watchlist_adds,
        AVG(d.avg_rating)                               AS avg_rating
    FROM analytics.mv_content_daily AS d
    JOIN core.content AS c USING (content_id)
    WHERE d.activity_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{content_filter}}
    GROUP BY c.genre_id
),
viewers AS (
    -- Distinct viewers per genre. Cannot be summed from the daily view without
    -- double-counting anyone who returned, so measured against the event table.
    SELECT
        c.genre_id,
        COUNT(DISTINCT e.user_id) AS unique_viewers
    FROM core.events  AS e
    JOIN core.content AS c ON c.content_id = e.content_id
    WHERE e.event_name = 'START_VIDEO'
      AND e.event_time >= CAST(:date_from AS date)
      AND e.event_time < (CAST(:date_to AS date) + INTERVAL '1 day')
    GROUP BY c.genre_id
)
SELECT
    cat.genre,
    cat.titles::bigint,
    cat.originals::bigint,
    cat.series_count::bigint,
    cat.avg_runtime_minutes,
    cat.avg_popularity,
    COALESCE(v.unique_viewers, 0)::bigint                            AS unique_viewers,
    COALESCE(e.starts, 0)::bigint                                    AS starts,
    COALESCE(e.completions, 0)::bigint                               AS completions,
    ROUND(COALESCE(e.watch_seconds, 0)::numeric / 3600.0, 1)         AS watch_hours,
    ROUND(100.0 * e.completions / NULLIF(e.starts, 0), 1)            AS completion_rate_pct,
    -- Discovery efficiency: of everyone who looked at a title in this genre, how
    -- many pressed play. Separates "hard to find" from "not appealing".
    ROUND(100.0 * e.starts / NULLIF(e.detail_views, 0), 1)           AS view_to_start_pct,
    ROUND(e.avg_rating::numeric, 2)                                  AS avg_rating,
    ROUND(100.0 * cat.titles / SUM(cat.titles) OVER (), 2)           AS catalogue_share_pct,
    ROUND(
        100.0 * COALESCE(e.watch_seconds, 0)
        / NULLIF(SUM(COALESCE(e.watch_seconds, 0)) OVER (), 0), 2
    )                                                                AS watch_share_pct,
    -- The commissioning signal. >1 earns more attention than its shelf space; <1 is
    -- over-invested relative to what it returns.
    ROUND(
        (COALESCE(e.watch_seconds, 0)::numeric
         / NULLIF(SUM(COALESCE(e.watch_seconds, 0)) OVER (), 0))
        / NULLIF(cat.titles::numeric / SUM(cat.titles) OVER (), 0), 2
    )                                                                AS watch_per_title_index,
    ROUND(
        COALESCE(e.watch_seconds, 0)::numeric / 3600.0 / NULLIF(cat.titles, 0), 1
    )                                                                AS watch_hours_per_title
FROM catalogue AS cat
LEFT JOIN engagement AS e USING (genre_id)
LEFT JOIN viewers    AS v USING (genre_id)
ORDER BY watch_hours DESC NULLS LAST
