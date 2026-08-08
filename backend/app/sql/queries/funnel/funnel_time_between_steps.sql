-- Elapsed time between consecutive funnel steps.
--
-- Answers a question the conversion percentages cannot: is the drop-off caused by
-- friction or by indifference? A long median from view to start suggests the detail
-- page is not persuading; a short one with heavy drop-off suggests the title itself
-- is the problem.
--
-- Medians and p90, never means. These distributions have a long right tail — a
-- session where someone opened a title, left the room, and came back an hour later
-- would drag a mean well past anything a real user experienced.
--
-- The timestamps come from mv_funnel_steps, which already carries the first
-- occurrence of each step per session, so no second pass over core.events is needed.
WITH scoped AS (
    SELECT
        f.ts_open_app,
        f.ts_first_search,
        f.ts_first_view,
        f.ts_first_start,
        f.ts_first_complete
    FROM analytics.mv_funnel_steps AS f
    JOIN core.users AS u USING (user_id)
    WHERE f.session_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
gaps AS (
    SELECT
        EXTRACT(EPOCH FROM (ts_first_search   - ts_open_app))     AS open_to_search,
        EXTRACT(EPOCH FROM (ts_first_view     - ts_open_app))     AS open_to_view,
        EXTRACT(EPOCH FROM (ts_first_start    - ts_first_view))   AS view_to_start,
        EXTRACT(EPOCH FROM (ts_first_complete - ts_first_start))  AS start_to_complete
    FROM scoped
),
long AS (
    SELECT 1 AS step_order, 'Open to first search'  AS transition, open_to_search    AS seconds FROM gaps
    UNION ALL SELECT 2, 'Open to first title view', open_to_view      FROM gaps
    UNION ALL SELECT 3, 'View to playback start',   view_to_start     FROM gaps
    UNION ALL SELECT 4, 'Start to completion',      start_to_complete FROM gaps
)
SELECT
    step_order,
    transition,
    COUNT(seconds)::bigint                                                AS observations,
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY seconds)::numeric, 1) AS p25_seconds,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY seconds)::numeric, 1) AS median_seconds,
    ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY seconds)::numeric, 1) AS p90_seconds,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY seconds)::numeric / 60.0, 2)
                                                                          AS median_minutes
FROM long
-- Negative gaps are impossible and would indicate a clock or ordering bug; a
-- non-empty result here with negatives filtered out is itself a quiet assertion.
WHERE seconds IS NOT NULL AND seconds >= 0
GROUP BY step_order, transition
ORDER BY step_order
