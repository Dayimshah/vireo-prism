-- Per-variant metrics for one experiment, shaped for a two-proportion z-test.
--
-- This query deliberately stops short of computing significance. It returns
-- numerators and denominators; app/services/stats.py turns them into a z-statistic,
-- a p-value and a Wilson interval. The split is not fussiness — significance testing
-- involves an inverse normal CDF and a decision rule, and putting that in SQL would
-- make it untestable and unreviewable.
--
-- The metric depends on the experiment, so `primary_metric` drives a CASE that picks
-- the right numerator. Every metric is reduced to a *binary outcome per user*, which
-- is what makes the two-proportion test valid:
--
--   subscription_conversion  did the user ever pay
--   completion_rate          did the user complete at least one title
--   day7_retention           was the user active on day 7
--   sessions_per_user        did the user exceed the median session count
--   trailer_to_start         did the user start a title after a trailer
--   session_duration         did the user exceed the median session length
--
-- Binarising a continuous metric loses information — a proper analysis of
-- sessions_per_user would use a t-test on counts — and that trade is stated in
-- docs/analytics-catalog.md rather than hidden. The gain is one uniform test with
-- one set of assumptions across every experiment, which is far easier to defend than
-- six bespoke tests.
--
-- Only post-assignment behaviour counts. Filtering on assigned_at is what keeps the
-- comparison causal: including activity from before a user was enrolled would
-- measure who they already were, not what the treatment did.
WITH experiment AS (
    SELECT
        e.experiment_id,
        e.key,
        e.name,
        e.primary_metric,
        e.variants,
        e.started_on,
        e.ended_on,
        e.status,
        e.traffic_allocation
    FROM core.experiments AS e
    WHERE e.key = CAST(:experiment_key AS text)
),
enrolled AS (
    SELECT
        a.user_id,
        a.variant,
        a.assigned_at,
        x.primary_metric,
        x.started_on,
        COALESCE(x.ended_on, CAST(:observation_end AS date)) AS window_end
    FROM core.experiment_assignments AS a
    JOIN experiment AS x USING (experiment_id)
    JOIN core.users  AS u USING (user_id)
    WHERE TRUE
      {{user_filter}}
),
-- Per-user activity inside the experiment window only.
activity AS (
    SELECT
        en.user_id,
        en.variant,
        en.primary_metric,
        COALESCE(SUM(d.sessions), 0)            AS sessions,
        COALESCE(SUM(d.completed_videos), 0)    AS completed_videos,
        COALESCE(SUM(d.started_videos), 0)      AS started_videos,
        COALESCE(SUM(d.watch_seconds), 0)       AS watch_seconds,
        BOOL_OR(d.days_since_signup = 7)        AS active_on_day7
    FROM enrolled AS en
    LEFT JOIN analytics.mv_user_daily AS d
           ON d.user_id = en.user_id
          AND d.activity_date >= en.assigned_at::date
          AND d.activity_date <= en.window_end
    GROUP BY en.user_id, en.variant, en.primary_metric
),
-- Medians for the two metrics that need a threshold. Computed across both arms
-- together: a per-arm median would move with the treatment effect and guarantee
-- ~50% in each arm, which would make the test structurally incapable of detecting
-- anything.
thresholds AS (
    SELECT
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY sessions)      AS median_sessions,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY watch_seconds) AS median_watch
    FROM activity
),
outcomes AS (
    SELECT
        a.variant,
        a.user_id,
        CASE a.primary_metric
            WHEN 'subscription_conversion' THEN
                EXISTS (SELECT 1 FROM core.subscriptions AS s
                        WHERE s.user_id = a.user_id AND s.mrr_usd > 0)
            WHEN 'completion_rate' THEN a.completed_videos > 0
            WHEN 'day7_retention'  THEN COALESCE(a.active_on_day7, false)
            WHEN 'sessions_per_user' THEN a.sessions > t.median_sessions
            WHEN 'session_duration'  THEN a.watch_seconds > t.median_watch
            WHEN 'trailer_to_start'  THEN a.started_videos > 0
            ELSE false
        END AS converted
    FROM activity AS a
    CROSS JOIN thresholds AS t
)
SELECT
    x.key                                                        AS experiment_key,
    x.name                                                       AS experiment_name,
    x.primary_metric,
    x.status,
    x.started_on,
    x.ended_on,
    ROUND(x.traffic_allocation, 2)                               AS traffic_allocation,
    o.variant,
    -- Control first, so the service layer never has to guess the baseline arm.
    (o.variant = 'control')                                      AS is_control,
    COUNT(*)::bigint                                             AS n,
    COUNT(*) FILTER (WHERE o.converted)::bigint                  AS successes,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE o.converted) / NULLIF(COUNT(*), 0), 3
    )                                                            AS rate_pct
FROM outcomes AS o
CROSS JOIN experiment AS x
GROUP BY x.key, x.name, x.primary_metric, x.status, x.started_on,
         x.ended_on, x.traffic_allocation, o.variant
ORDER BY is_control DESC, o.variant
