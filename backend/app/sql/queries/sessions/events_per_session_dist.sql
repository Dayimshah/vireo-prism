-- Distribution of events per session, bucketed.
--
-- Bucketed rather than reported as a mean, because the mean of a right-skewed
-- distribution describes nobody. The buckets are chosen to separate behaviours
-- that mean different things: a 2-event session is a bounce (open, leave), 3-5 is
-- a browse, and anything past 20 is a genuine viewing session.
--
-- width_bucket is deliberately avoided here — its uniform widths would put a
-- 2-event bounce and a 9-event browse in the same bin, which is the distinction
-- the chart exists to show.
SELECT
    CASE
        WHEN f.event_count <= 2  THEN '2 (bounce)'
        WHEN f.event_count <= 5  THEN '3-5'
        WHEN f.event_count <= 10 THEN '6-10'
        WHEN f.event_count <= 20 THEN '11-20'
        WHEN f.event_count <= 40 THEN '21-40'
        ELSE '40+'
    END                                                    AS bucket,
    -- Explicit sort key: the labels above sort lexically into nonsense ('11-20'
    -- before '2'), so ordering must be numeric and independent of the label.
    CASE
        WHEN f.event_count <= 2  THEN 1
        WHEN f.event_count <= 5  THEN 2
        WHEN f.event_count <= 10 THEN 3
        WHEN f.event_count <= 20 THEN 4
        WHEN f.event_count <= 40 THEN 5
        ELSE 6
    END                                                    AS bucket_order,
    COUNT(*)::bigint                                       AS sessions,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)     AS pct_of_sessions,
    ROUND(AVG(f.watch_seconds)::numeric / 60.0, 1)         AS avg_watch_minutes,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.did_start_video)
          / NULLIF(COUNT(*), 0), 1)                        AS pct_with_playback
FROM analytics.mv_funnel_steps AS f
JOIN core.users AS u USING (user_id)
WHERE f.session_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
  {{user_filter}}
GROUP BY bucket, bucket_order
ORDER BY bucket_order
