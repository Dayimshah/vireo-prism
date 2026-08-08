-- Trailer-to-start conversion per title.
--
-- Measures whether a trailer sells the title or talks people out of it. Both
-- outcomes are legitimate — a trailer that filters out the wrong audience improves
-- completion rate even as it lowers starts — so this query reports the pair rather
-- than ranking on conversion alone.
--
-- The diagnostic combination is `trailer_to_start_pct` against
-- `completion_rate_pct`:
--
--   low start / high completion   trailer is filtering well
--   high start / low completion   trailer oversells; viewers bounce
--   low start / low completion    the title has a real problem
--
-- `lift_vs_no_trailer` is the honest test of whether the trailer helps at all. It
-- compares the start rate among users who watched the trailer against those who
-- reached the detail page and did not, so a value below 1.0 means the trailer is
-- costing starts.
WITH detail_sessions AS (
    -- One row per (session, title) that reached the detail page, with what happened
    -- next. Grain matters: a session can view several titles, and per-session
    -- aggregation would attribute one title's trailer to another's start.
    SELECT
        e.session_id,
        e.content_id,
        BOOL_OR(e.event_name = 'WATCH_TRAILER') AS saw_trailer,
        BOOL_OR(e.event_name = 'START_VIDEO')   AS started,
        BOOL_OR(e.event_name = 'COMPLETE_VIDEO') AS completed
    FROM core.events AS e
    JOIN core.users AS u USING (user_id)
    WHERE e.content_id IS NOT NULL
      AND e.event_name IN ('VIEW_CONTENT', 'WATCH_TRAILER', 'START_VIDEO', 'COMPLETE_VIDEO')
      AND e.event_time >= CAST(:date_from AS date)
      AND e.event_time < (CAST(:date_to AS date) + INTERVAL '1 day')
      {{user_filter}}
    GROUP BY e.session_id, e.content_id
),
per_title AS (
    SELECT
        content_id,
        COUNT(*)                                                          AS detail_views,
        COUNT(*) FILTER (WHERE saw_trailer)                               AS trailer_views,
        COUNT(*) FILTER (WHERE saw_trailer AND started)                   AS trailer_then_start,
        COUNT(*) FILTER (WHERE NOT saw_trailer AND started)               AS start_without_trailer,
        COUNT(*) FILTER (WHERE NOT saw_trailer)                           AS no_trailer_views,
        COUNT(*) FILTER (WHERE started)                                   AS starts,
        COUNT(*) FILTER (WHERE completed)                                 AS completions
    FROM detail_sessions
    GROUP BY content_id
)
SELECT
    c.title,
    g.name                                                    AS genre,
    c.content_type::text                                      AS content_type,
    c.is_original,
    t.detail_views::bigint,
    t.trailer_views::bigint,
    t.starts::bigint,
    ROUND(100.0 * t.trailer_views / NULLIF(t.detail_views, 0), 1)
                                                              AS trailer_view_rate_pct,
    ROUND(100.0 * t.trailer_then_start / NULLIF(t.trailer_views, 0), 1)
                                                              AS trailer_to_start_pct,
    ROUND(100.0 * t.start_without_trailer / NULLIF(t.no_trailer_views, 0), 1)
                                                              AS start_without_trailer_pct,
    -- Ratio of the two rates above. Above 1.0, the trailer earns its place.
    ROUND(
        (t.trailer_then_start::numeric / NULLIF(t.trailer_views, 0))
        / NULLIF(t.start_without_trailer::numeric / NULLIF(t.no_trailer_views, 0), 0), 2
    )                                                         AS lift_vs_no_trailer,
    ROUND(100.0 * t.completions / NULLIF(t.starts, 0), 1)     AS completion_rate_pct
FROM per_title AS t
JOIN core.content AS c USING (content_id)
JOIN core.genres  AS g ON g.genre_id = c.genre_id
WHERE TRUE
  {{content_filter}}
  -- Small denominators produce conversion rates of 0% or 100% that mean nothing.
  --
  -- WHERE, not HAVING: every aggregate was already computed in the per_title CTE, so
  -- this outer query has no GROUP BY. A HAVING here would make PostgreSQL treat the
  -- whole result as a single aggregate group and reject c.title as ungrouped.
  AND t.trailer_views >= CAST(:min_starts AS int)
ORDER BY trailer_to_start_pct DESC NULLS LAST
LIMIT CAST(:limit AS int)
