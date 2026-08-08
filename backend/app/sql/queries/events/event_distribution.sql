-- Event volume by name and screen: the clickstream's composition.
--
-- The "we really do have event-level data" query, and a useful sanity check on the
-- whole pipeline. The expected shape is specific and checkable: VIDEO_PROGRESS
-- dominates (one checkpoint per ten minutes watched), navigation events outnumber
-- playback events, and starts exceed completions. If completions ever exceeded
-- starts, the journey invariants would be broken and every content metric wrong.
--
-- Reads core.events directly rather than a materialized view, because this is the one
-- question that is genuinely about raw event composition — aggregating it first would
-- discard exactly what is being measured.
--
-- Performance note: GROUPING SETS, so the event table is scanned once.
--
-- Two grains are needed — per event_name, and per (event_name, screen) for the screen
-- mix. Computing them as separate CTEs scans 1.09M rows twice and measured 3.6s. A
-- correlated subquery per event name was worse still, at 11s.
--
-- The per-event-name totals cannot be derived by summing the per-screen rows, because
-- COUNT(DISTINCT session_id) does not add: one session can produce EXIT events on
-- different screens and would be counted once per screen. GROUPING SETS asks
-- PostgreSQL for both grains in a single pass, with each distinct count computed
-- correctly at its own level.
WITH grouped AS (
    SELECT
        e.event_name::text                        AS event_name,
        e.screen                                  AS screen,
        -- 1 for the (event_name) set, 0 for (event_name, screen).
        GROUPING(e.screen)                        AS is_event_total,
        COUNT(*)                                  AS events,
        COUNT(DISTINCT e.session_id)              AS sessions,
        COUNT(DISTINCT e.user_id)                 AS users,
        COUNT(DISTINCT e.content_id)              AS distinct_content,
        SUM(COALESCE(e.watch_seconds, 0))         AS watch_seconds
    FROM core.events AS e
    JOIN core.users AS u USING (user_id)
    WHERE e.event_time >= CAST(:date_from AS date)
      AND e.event_time < (CAST(:date_to AS date) + INTERVAL '1 day')
      {{user_filter}}
    GROUP BY GROUPING SETS ((e.event_name), (e.event_name, e.screen))
),
totals AS (
    SELECT event_name, events, sessions, users, distinct_content, watch_seconds
    FROM grouped
    WHERE is_event_total = 1
),
screen_mix AS (
    SELECT event_name, jsonb_object_agg(screen, events) AS screen_mix
    FROM grouped
    WHERE is_event_total = 0
    GROUP BY event_name
)
SELECT
    t.event_name,
    -- Classification the frontend would otherwise have to hard-code, and which would
    -- then drift from the enum in Alembic revision 0001.
    CASE
        WHEN t.event_name IN ('OPEN_APP', 'HOME', 'BROWSE_GENRE', 'SEARCH', 'EXIT')
            THEN 'navigation'
        WHEN t.event_name IN ('VIEW_CONTENT', 'WATCH_TRAILER', 'ADD_TO_WATCHLIST')
            THEN 'discovery'
        WHEN t.event_name IN ('START_VIDEO', 'VIDEO_PROGRESS', 'PAUSE_VIDEO',
                              'ABANDON_VIDEO', 'COMPLETE_VIDEO')
            THEN 'playback'
        WHEN t.event_name IN ('RATE', 'SUBSCRIBE_CLICK')
            THEN 'conversion'
        ELSE 'other'
    END                                                              AS event_category,
    t.events::bigint,
    t.sessions::bigint,
    t.users::bigint,
    t.distinct_content::bigint,
    ROUND(t.watch_seconds::numeric / 3600.0, 1)                      AS watch_hours,
    ROUND(100.0 * t.events / NULLIF(SUM(t.events) OVER (), 0), 2)    AS pct_of_events,
    ROUND(t.events::numeric / NULLIF(t.sessions, 0), 2)              AS events_per_session,
    -- Funnel reach: OPEN_APP occurs in every session, so its session count is the
    -- denominator for "what share of sessions reached this event at all".
    ROUND(
        100.0 * t.sessions
        / NULLIF(MAX(t.sessions) FILTER (WHERE t.event_name = 'OPEN_APP')
                 OVER (), 0), 2
    )                                                                AS pct_of_sessions_reached,
    -- Screen mix per event, as a JSON object. One query then serves both the event bar
    -- chart and the per-event screen drilldown, rather than the frontend issuing a
    -- request per event name.
    m.screen_mix
FROM totals AS t
LEFT JOIN screen_mix AS m USING (event_name)
ORDER BY t.events DESC
