-- How deep into a session users get, measured by furthest step reached.
--
-- Distinct from events_per_session_dist: that counts *volume* of interaction, this
-- measures *progress*. A user can generate thirty events by scrolling the home rails
-- without ever reaching a title, which reads as a deep session by event count and a
-- shallow one here. The gap between the two charts is where indecision lives.
--
-- Depth is derived from the boolean step flags rather than max_step_index, because a
-- raw step index conflates volume with progress in exactly the way this query exists
-- to separate.
WITH scoped AS (
    SELECT
        f.session_id,
        f.event_count,
        f.watch_seconds,
        f.max_step_index,
        CASE
            WHEN f.did_complete_video THEN 5
            WHEN f.did_start_video    THEN 4
            WHEN f.did_view_content   THEN 3
            WHEN f.did_search OR f.did_browse THEN 2
            ELSE 1
        END AS depth
    FROM analytics.mv_funnel_steps AS f
    JOIN core.users AS u USING (user_id)
    WHERE f.session_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
)
SELECT
    depth                                                            AS depth_level,
    CASE depth
        WHEN 1 THEN 'Opened only'
        WHEN 2 THEN 'Browsed or searched'
        WHEN 3 THEN 'Reached a title'
        WHEN 4 THEN 'Started playback'
        WHEN 5 THEN 'Completed something'
    END                                                              AS depth_label,
    COUNT(*)::bigint                                                 AS sessions,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)               AS pct_of_sessions,
    ROUND(AVG(event_count)::numeric, 1)                              AS avg_events,
    ROUND(AVG(max_step_index)::numeric, 1)                           AS avg_max_step,
    ROUND(AVG(watch_seconds)::numeric / 60.0, 1)                     AS avg_watch_minutes,
    -- Cumulative share reaching at least this depth: the funnel view of the same data.
    ROUND(
        100.0 * SUM(COUNT(*)) OVER (ORDER BY depth DESC)
        / SUM(COUNT(*)) OVER (), 2
    )                                                                AS pct_reaching_at_least
FROM scoped
GROUP BY depth
ORDER BY depth
