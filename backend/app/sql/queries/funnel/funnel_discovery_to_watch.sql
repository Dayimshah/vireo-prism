-- Discovery-to-watch funnel, session-scoped.
--
-- Reads analytics.mv_funnel_steps, which is one row per session with a boolean per
-- step. That reshape is what makes an eight-step funnel eight filtered counts over
-- one sequential scan, instead of eight self-joins against the event table.
--
-- Steps are *strictly cumulative*: reaching a step requires having reached every
-- prior one. Counting each step independently is the common mistake and it produces
-- funnels that widen in the middle, which is impossible and immediately undermines
-- the chart. Discovery is the one exception — a user may arrive at a title by
-- browsing or by searching, so that step is a disjunction rather than a conjunction.
WITH scoped AS (
    SELECT f.*
    FROM analytics.mv_funnel_steps AS f
    JOIN core.users AS u USING (user_id)
    WHERE f.session_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
steps AS (
    SELECT
        COUNT(*)                                                   AS s1_opened,
        COUNT(*) FILTER (WHERE did_home OR did_browse OR did_search) AS s2_discovered,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content)                   AS s3_viewed,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content
                           AND did_start_video)                    AS s4_started,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content
                           AND did_start_video
                           AND did_complete_video)                 AS s5_completed,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content
                           AND did_start_video
                           AND did_complete_video
                           AND did_rate)                           AS s6_rated
    FROM scoped
),
long AS (
    SELECT 1 AS step_order, 'Opened app'      AS step_name, s1_opened   AS sessions FROM steps
    UNION ALL SELECT 2, 'Browsed or searched', s2_discovered FROM steps
    UNION ALL SELECT 3, 'Viewed a title',      s3_viewed     FROM steps
    UNION ALL SELECT 4, 'Started playback',    s4_started    FROM steps
    UNION ALL SELECT 5, 'Completed',           s5_completed  FROM steps
    UNION ALL SELECT 6, 'Rated',               s6_rated      FROM steps
)
SELECT
    step_order,
    step_name,
    sessions::bigint,
    -- Conversion from the top of the funnel.
    ROUND(100.0 * sessions
          / NULLIF(FIRST_VALUE(sessions) OVER (ORDER BY step_order), 0), 2)
                                                              AS pct_of_entry,
    -- Conversion from the immediately preceding step. The more actionable of the
    -- two: it localises where the loss actually happens.
    ROUND(100.0 * sessions
          / NULLIF(LAG(sessions) OVER (ORDER BY step_order), 0), 2)
                                                              AS pct_of_previous,
    (LAG(sessions) OVER (ORDER BY step_order) - sessions)::bigint
                                                              AS dropped_from_previous
FROM long
ORDER BY step_order
