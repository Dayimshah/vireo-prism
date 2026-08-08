-- The discovery funnel split by a caller-chosen dimension.
--
-- Segment resolution follows the same pattern as retention_by_segment: a bound
-- :segment_by parameter through a CASE, never an interpolated column name. The CASE
-- arms are the allowlist, so an unrecognised value collapses to 'all' rather than
-- reaching the planner as text.
--
-- Comparing funnels across segments is where funnel analysis becomes useful. An
-- aggregate 30% view-to-start rate is a number; discovering that it is 55% on TV and
-- 18% on phones is a decision.
WITH scoped AS (
    SELECT
        f.did_home, f.did_browse, f.did_search, f.did_view_content,
        f.did_start_video, f.did_complete_video,
        CASE CAST(:segment_by AS text)
            WHEN 'country'     THEN co.name
            WHEN 'channel'     THEN ch.name
            WHEN 'persona'     THEN p.name
            WHEN 'form_factor' THEN dv.form_factor
            WHEN 'platform'    THEN dv.platform
            WHEN 'premium'     THEN CASE WHEN u.is_premium THEN 'premium' ELSE 'free' END
            ELSE 'all'
        END AS segment
    FROM analytics.mv_funnel_steps AS f
    JOIN core.users              AS u  USING (user_id)
    JOIN core.countries          AS co ON co.country_id = u.country_id
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    JOIN core.personas           AS p  ON p.persona_id  = u.persona_id
    -- Session device, not signup device: the funnel is a property of the visit.
    JOIN core.sessions           AS s  ON s.session_id = f.session_id
    JOIN core.devices            AS dv ON dv.device_id = s.device_id
    WHERE f.session_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
steps AS (
    SELECT
        segment,
        COUNT(*)                                                     AS s1_opened,
        COUNT(*) FILTER (WHERE did_home OR did_browse OR did_search) AS s2_discovered,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content)                     AS s3_viewed,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content AND did_start_video) AS s4_started,
        COUNT(*) FILTER (WHERE (did_home OR did_browse OR did_search)
                           AND did_view_content AND did_start_video
                           AND did_complete_video)                   AS s5_completed
    FROM scoped
    GROUP BY segment
)
SELECT
    segment,
    s1_opened::bigint                                             AS opened,
    s2_discovered::bigint                                         AS discovered,
    s3_viewed::bigint                                             AS viewed,
    s4_started::bigint                                            AS started,
    s5_completed::bigint                                          AS completed,
    ROUND(100.0 * s3_viewed   / NULLIF(s1_opened, 0), 2)          AS open_to_view_pct,
    -- The step that varies most between segments, and the one worth ranking on.
    ROUND(100.0 * s4_started  / NULLIF(s3_viewed, 0), 2)          AS view_to_start_pct,
    ROUND(100.0 * s5_completed / NULLIF(s4_started, 0), 2)        AS start_to_complete_pct,
    ROUND(100.0 * s5_completed / NULLIF(s1_opened, 0), 2)         AS end_to_end_pct
FROM steps
WHERE s1_opened >= CAST(:min_cohort_size AS int)
ORDER BY end_to_end_pct DESC NULLS LAST
