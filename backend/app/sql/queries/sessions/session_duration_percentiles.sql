-- Session duration percentiles, overall and by device form factor.
--
-- Percentiles rather than a mean, and the reason is visible in the output: session
-- durations are heavily right-skewed, so the mean sits well above the median and
-- describes a session almost nobody has. Reporting p50 alongside p95 makes the
-- skew explicit instead of hiding it in an average.
--
-- The form-factor split is the most reliable finding on the Sessions page: a TV
-- session runs several times longer than a phone session, because a phone session
-- is often a two-minute check of what is new. Grouping by form_factor rather than
-- device name keeps iOS and Android phones together, which is the distinction that
-- actually predicts behaviour.
WITH scoped AS (
    SELECT
        s.duration_seconds,
        s.watch_seconds,
        s.event_count,
        dv.form_factor,
        dv.platform
    FROM core.sessions AS s
    JOIN core.users   AS u USING (user_id)
    JOIN core.devices AS dv ON dv.device_id = s.device_id
    WHERE s.session_start >= CAST(:date_from AS date)
      AND s.session_start < (CAST(:date_to AS date) + INTERVAL '1 day')
      {{user_filter}}
),
by_factor AS (
    SELECT
        form_factor                                                       AS dimension,
        'form_factor'                                                     AS dimension_type,
        COUNT(*)                                                          AS sessions,
        AVG(duration_seconds)                                             AS mean_seconds,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY duration_seconds)     AS p25,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_seconds)     AS p50,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY duration_seconds)     AS p75,
        PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY duration_seconds)     AS p90,
        PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_seconds)     AS p99,
        AVG(watch_seconds)                                                AS mean_watch,
        AVG(event_count)                                                  AS mean_events
    FROM scoped
    GROUP BY form_factor
),
overall AS (
    SELECT
        'All devices'                                                     AS dimension,
        'overall'                                                         AS dimension_type,
        COUNT(*)                                                          AS sessions,
        AVG(duration_seconds)                                             AS mean_seconds,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY duration_seconds)     AS p25,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_seconds)     AS p50,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY duration_seconds)     AS p75,
        PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY duration_seconds)     AS p90,
        PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_seconds)     AS p99,
        AVG(watch_seconds)                                                AS mean_watch,
        AVG(event_count)                                                  AS mean_events
    FROM scoped
)
SELECT
    dimension_type,
    dimension,
    sessions::bigint,
    ROUND(mean_seconds::numeric / 60.0, 1)      AS mean_minutes,
    ROUND(p25::numeric / 60.0, 1)               AS p25_minutes,
    ROUND(p50::numeric / 60.0, 1)               AS median_minutes,
    ROUND(p75::numeric / 60.0, 1)               AS p75_minutes,
    ROUND(p90::numeric / 60.0, 1)               AS p90_minutes,
    ROUND(p99::numeric / 60.0, 1)               AS p99_minutes,
    ROUND(mean_watch::numeric / 60.0, 1)        AS mean_watch_minutes,
    ROUND(mean_events::numeric, 1)              AS mean_events,
    -- Share of session time actually spent watching. The gap from 100% is browsing,
    -- deciding and paused playback.
    ROUND(100.0 * mean_watch / NULLIF(mean_seconds, 0), 1)
                                                AS watch_share_pct
FROM (SELECT * FROM overall UNION ALL SELECT * FROM by_factor) AS combined
-- 'overall' sorts before 'form_factor', putting the headline row first.
ORDER BY dimension_type, sessions DESC
