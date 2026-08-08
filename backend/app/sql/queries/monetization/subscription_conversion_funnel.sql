-- Conversion rate by engagement decile: the planted signal, recovered.
--
-- This is the query that justifies the whole simulation design, and the one to put
-- in front of a sceptical reviewer. seeder/config.py declares that conversion is a
-- logistic function of trailing watch time, completions and searches. This query
-- reads none of that. It buckets users by watch time measured from the event stream
-- and counts who paid.
--
-- The expected output is a monotonic gradient — roughly 0% in the lowest deciles
-- rising past 30% in the highest. That ordering was not written anywhere in SQL; it
-- emerged from the generator and was recovered here. A flat line would mean the
-- dataset has no causal structure and every other chart in the project is decoration.
--
-- Deciles rather than fixed watch-hour thresholds, so the buckets stay populated
-- regardless of profile size and the chart never has empty bars at one end.
WITH cohort AS (
    SELECT
        l.user_id,
        l.total_watch_seconds,
        l.completed_videos,
        l.searches,
        l.total_sessions,
        l.active_days
    FROM analytics.mv_user_lifetime AS l
    JOIN core.users AS u USING (user_id)
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
deciled AS (
    SELECT
        *,
        NTILE(10) OVER (ORDER BY total_watch_seconds) AS watch_decile
    FROM cohort
),
outcomes AS (
    SELECT
        d.*,
        EXISTS (
            SELECT 1 FROM core.subscriptions AS s
            WHERE s.user_id = d.user_id AND s.status = 'trialing'
        ) AS started_trial,
        EXISTS (
            SELECT 1 FROM core.subscriptions AS s
            WHERE s.user_id = d.user_id AND s.mrr_usd > 0
        ) AS paid,
        EXISTS (
            SELECT 1 FROM core.subscriptions AS s
            WHERE s.user_id = d.user_id AND s.mrr_usd > 0 AND s.ended_on IS NULL
        ) AS still_paying
    FROM deciled AS d
)
SELECT
    watch_decile,
    COUNT(*)::bigint                                                  AS users,
    ROUND(MIN(total_watch_seconds)::numeric / 3600.0, 2)              AS min_watch_hours,
    ROUND(MAX(total_watch_seconds)::numeric / 3600.0, 2)              AS max_watch_hours,
    ROUND(AVG(total_watch_seconds)::numeric / 3600.0, 2)              AS avg_watch_hours,
    ROUND(AVG(completed_videos)::numeric, 1)                          AS avg_completions,
    ROUND(AVG(total_sessions)::numeric, 1)                            AS avg_sessions,
    COUNT(*) FILTER (WHERE started_trial)::bigint                     AS started_trial,
    COUNT(*) FILTER (WHERE paid)::bigint                              AS converted_paid,
    COUNT(*) FILTER (WHERE still_paying)::bigint                      AS still_paying,
    ROUND(100.0 * COUNT(*) FILTER (WHERE started_trial) / NULLIF(COUNT(*), 0), 2)
                                                                      AS trial_rate_pct,
    -- The headline column: the gradient across this is the recovered signal.
    ROUND(100.0 * COUNT(*) FILTER (WHERE paid) / NULLIF(COUNT(*), 0), 2)
                                                                      AS conversion_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE still_paying)
          / NULLIF(COUNT(*) FILTER (WHERE paid), 0), 2)               AS paid_retention_pct,
    -- Lift over the population base rate, which makes the gradient legible without
    -- the reader having to compute ratios from the percentages.
    ROUND(
        (COUNT(*) FILTER (WHERE paid)::numeric / NULLIF(COUNT(*), 0))
        / NULLIF(SUM(COUNT(*) FILTER (WHERE paid)) OVER ()::numeric
                 / NULLIF(SUM(COUNT(*)) OVER (), 0), 0), 2
    )                                                                 AS conversion_lift
FROM outcomes
GROUP BY watch_decile
ORDER BY watch_decile
