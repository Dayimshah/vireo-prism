-- Drop-off between funnel steps, ranked by where the largest loss occurs.
--
-- The same underlying counts as funnel_discovery_to_watch, reoriented to answer the
-- question a product manager actually asks: not "what is our conversion" but "where
-- should we spend next quarter".
--
-- Two different rankings are returned because they disagree, and the disagreement is
-- the insight. `loss_rank` finds the step losing the most *users* — usually near the
-- top of the funnel, where the volume is. `rate_rank` finds the step with the worst
-- *conversion* — usually deeper, where a smaller number of highly-intentioned users
-- fall out. Fixing the first moves the headline number; fixing the second is often
-- cheaper and more tractable.
WITH scoped AS (
    SELECT f.*
    FROM analytics.mv_funnel_steps AS f
    JOIN core.users AS u USING (user_id)
    WHERE f.session_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
steps AS (
    SELECT
        COUNT(*)                                                     AS s1,
        COUNT(*) FILTER (WHERE did_home OR did_browse OR did_search) AS s2,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content)                     AS s3,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content AND did_start_video) AS s4,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content AND did_start_video
                           AND did_complete_video)                   AS s5
    FROM scoped
),
transitions AS (
    SELECT 1 AS step_order, 'Opened'  AS from_step, 'Discovered' AS to_step, s1 AS from_count, s2 AS to_count FROM steps
    UNION ALL SELECT 2, 'Discovered', 'Viewed a title', s2, s3 FROM steps
    UNION ALL SELECT 3, 'Viewed a title', 'Started playback', s3, s4 FROM steps
    UNION ALL SELECT 4, 'Started playback', 'Completed', s4, s5 FROM steps
)
SELECT
    step_order,
    from_step,
    to_step,
    from_count::bigint,
    to_count::bigint,
    (from_count - to_count)::bigint                                  AS users_lost,
    ROUND(100.0 * (from_count - to_count) / NULLIF(from_count, 0), 2) AS dropoff_pct,
    ROUND(100.0 * to_count / NULLIF(from_count, 0), 2)               AS conversion_pct,
    -- Absolute loss: where the most sessions disappear.
    RANK() OVER (ORDER BY (from_count - to_count) DESC)              AS loss_rank,
    -- Relative loss: where the funnel is leakiest proportionally.
    RANK() OVER (ORDER BY (from_count - to_count)::numeric
                          / NULLIF(from_count, 0) DESC)              AS rate_rank
FROM transitions
ORDER BY step_order
