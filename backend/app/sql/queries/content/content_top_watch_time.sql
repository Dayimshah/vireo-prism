-- Content leaderboard by total watch time.
--
-- Watch time rather than start count, because starts reward promotion while watch
-- time rewards what people actually sat through. A heavily merchandised title can
-- top a starts leaderboard while contributing little viewing.
--
-- watch_hours_per_viewer separates the two shapes of a hit: broad reach with light
-- viewing (a film everyone samples) against narrow reach with deep viewing (a series
-- a smaller audience finishes). Both are valuable and they call for different
-- decisions, so the leaderboard reports the ratio alongside the total.
--
-- Performance note: rank first, then enrich.
--
-- Distinct viewers cannot be summed from mv_content_daily — a viewer active on three
-- days would count three times — so it has to be measured against core.events. Doing
-- that for the whole catalogue costs a scan of every playback event, and the query
-- then discards all but 20 rows. Measured 3.9s.
--
-- Ranking from the materialized view first and restricting the viewer count to the
-- surviving content_ids does the same work for 20 titles instead of 320, and lets the
-- planner use ix_events_content_time as an index scan rather than a full pass.
WITH ranked AS (
    SELECT
        c.content_id,
        SUM(d.watch_seconds)  AS watch_seconds,
        SUM(d.starts)         AS starts,
        SUM(d.completions)    AS completions,
        SUM(d.detail_views)   AS detail_views,
        SUM(d.watchlist_adds) AS watchlist_adds
    FROM analytics.mv_content_daily AS d
    JOIN core.content AS c USING (content_id)
    WHERE d.activity_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{content_filter}}
    GROUP BY c.content_id
    ORDER BY SUM(d.watch_seconds) DESC
    LIMIT CAST(:limit AS int)
),
viewers AS (
    SELECT
        e.content_id,
        COUNT(DISTINCT e.user_id) AS unique_viewers
    FROM core.events AS e
    -- The restriction that makes this cheap.
    WHERE e.content_id IN (SELECT content_id FROM ranked)
      AND e.event_name = 'START_VIDEO'
      AND e.event_time >= CAST(:date_from AS date)
      AND e.event_time < (CAST(:date_to AS date) + INTERVAL '1 day')
    GROUP BY e.content_id
)
SELECT
    c.content_id,
    c.title,
    g.name                                                    AS genre,
    c.content_type::text                                      AS content_type,
    c.release_year,
    c.language,
    c.is_original,
    c.runtime_minutes,
    ROUND(r.watch_seconds::numeric / 3600.0, 1)               AS watch_hours,
    r.starts::bigint                                          AS starts,
    r.completions::bigint                                     AS completions,
    r.detail_views::bigint                                    AS detail_views,
    r.watchlist_adds::bigint                                  AS watchlist_adds,
    COALESCE(v.unique_viewers, 0)::bigint                     AS unique_viewers,
    ROUND(
        r.watch_seconds::numeric / 3600.0 / NULLIF(v.unique_viewers, 0), 2
    )                                                         AS watch_hours_per_viewer,
    ROUND(100.0 * r.completions / NULLIF(r.starts, 0), 1)     AS completion_rate_pct,
    -- Rank within the returned leaderboard, which is what the UI displays.
    RANK() OVER (ORDER BY r.watch_seconds DESC)               AS watch_rank
FROM ranked AS r
JOIN core.content AS c USING (content_id)
JOIN core.genres  AS g ON g.genre_id = c.genre_id
LEFT JOIN viewers AS v USING (content_id)
ORDER BY r.watch_seconds DESC
