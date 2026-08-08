-- Device and platform breakdown, split by signup surface against usage surface.
--
-- The finding this exists to surface: phones dominate *signup* while televisions
-- dominate *watch time*. Both are true, they describe different moments, and a report
-- that measures only one of them draws the wrong conclusion about where to invest.
--
-- Two row types, because the two questions need different denominators:
--
--   signup  grouped by core.users.device_id  — where accounts are created
--   usage   grouped by core.sessions.device_id — where watching happens
--
-- Keeping them in one result set lets the dashboard put the two bars side by side,
-- which is the comparison that makes the point. Grouping by form_factor rather than
-- device name keeps iOS and Android phones together, since the screen predicts
-- behaviour and the OS does not.
WITH signup_side AS (
    SELECT
        'signup'                                        AS row_type,
        dv.form_factor                                  AS form_factor,
        dv.platform                                     AS platform,
        COUNT(*)                                        AS users,
        SUM(COALESCE(l.total_watch_seconds, 0))         AS watch_seconds,
        SUM(COALESCE(l.total_sessions, 0))              AS sessions,
        AVG(l.completion_rate)                          AS avg_completion_rate,
        SUM(COALESCE(l.lifetime_revenue_usd, 0))        AS revenue_usd,
        COUNT(*) FILTER (WHERE l.lifetime_revenue_usd > 0) AS paying_users,
        NULL::numeric                                   AS avg_session_minutes
    FROM core.users   AS u
    JOIN core.devices AS dv ON dv.device_id = u.device_id
    LEFT JOIN analytics.mv_user_lifetime AS l USING (user_id)
    WHERE u.signup_date <= CAST(:date_to AS date)
      {{user_filter}}
    GROUP BY dv.form_factor, dv.platform
),
usage_side AS (
    SELECT
        'usage'                                         AS row_type,
        dv.form_factor                                  AS form_factor,
        dv.platform                                     AS platform,
        COUNT(DISTINCT s.user_id)                       AS users,
        SUM(s.watch_seconds)                            AS watch_seconds,
        COUNT(*)                                        AS sessions,
        NULL::numeric                                   AS avg_completion_rate,
        NULL::numeric                                   AS revenue_usd,
        NULL::bigint                                    AS paying_users,
        -- The column that explains the divergence: a TV session runs several times
        -- longer than a phone session.
        AVG(s.duration_seconds)::numeric / 60.0         AS avg_session_minutes
    FROM core.sessions AS s
    JOIN core.users    AS u USING (user_id)
    JOIN core.devices  AS dv ON dv.device_id = s.device_id
    WHERE s.session_start >= CAST(:date_from AS date)
      AND s.session_start < (CAST(:date_to AS date) + INTERVAL '1 day')
      {{user_filter}}
    GROUP BY dv.form_factor, dv.platform
),
combined AS (
    SELECT * FROM signup_side
    UNION ALL
    SELECT * FROM usage_side
)
SELECT
    row_type,
    form_factor,
    platform,
    users::bigint,
    sessions::bigint,
    ROUND(watch_seconds::numeric / 3600.0, 1)                        AS watch_hours,
    ROUND(avg_session_minutes, 1)                                    AS avg_session_minutes,
    ROUND(avg_completion_rate::numeric, 3)                           AS avg_completion_rate,
    ROUND(revenue_usd, 2)                                            AS revenue_usd,
    paying_users,
    ROUND(100.0 * paying_users / NULLIF(users, 0), 2)                AS conversion_pct,
    -- Shares are computed within a row_type, so signup shares and usage shares each
    -- sum to 100% independently and the two are directly comparable.
    ROUND(
        100.0 * users / NULLIF(SUM(users) OVER (PARTITION BY row_type), 0), 2
    )                                                                AS share_of_users_pct,
    ROUND(
        100.0 * watch_seconds
        / NULLIF(SUM(watch_seconds) OVER (PARTITION BY row_type), 0), 2
    )                                                                AS share_of_watch_pct,
    ROUND(
        100.0 * sessions
        / NULLIF(SUM(sessions) OVER (PARTITION BY row_type), 0), 2
    )                                                                AS share_of_sessions_pct
FROM combined
ORDER BY row_type, watch_hours DESC NULLS LAST
