-- Where sessions begin and where they end.
--
-- The exit-screen distribution is the more useful half. An exit from `player` is a
-- satisfied user who finished watching; an exit from `paywall` is a user who was
-- blocked; an exit from `search` means they looked for something and did not find
-- it. Those three are the same event in the log and completely different products
-- problems.
--
-- Returned as a matrix of entry x exit pairs plus marginals, so the dashboard can
-- render either a Sankey or two bar charts from one query.
WITH scoped AS (
    SELECT
        s.entry_screen,
        s.exit_screen,
        s.duration_seconds,
        s.watch_seconds,
        s.is_first_session
    FROM core.sessions AS s
    JOIN core.users AS u USING (user_id)
    WHERE s.session_start >= CAST(:date_from AS date)
      AND s.session_start < (CAST(:date_to AS date) + INTERVAL '1 day')
      {{user_filter}}
),
pairs AS (
    SELECT
        entry_screen,
        exit_screen,
        COUNT(*)                                        AS sessions,
        AVG(duration_seconds)                           AS mean_seconds,
        SUM(watch_seconds)                              AS watch_seconds,
        COUNT(*) FILTER (WHERE is_first_session)        AS first_sessions
    FROM scoped
    GROUP BY entry_screen, exit_screen
)
SELECT
    entry_screen,
    exit_screen,
    sessions::bigint,
    ROUND(100.0 * sessions / SUM(sessions) OVER (), 2)              AS pct_of_all,
    -- Share of sessions that entered here and left there, which is what makes the
    -- row readable as "of everyone who started on home, N% left from the player".
    ROUND(100.0 * sessions / SUM(sessions) OVER (PARTITION BY entry_screen), 2)
                                                                    AS pct_of_entry_screen,
    ROUND(mean_seconds::numeric / 60.0, 1)                          AS mean_minutes,
    ROUND(watch_seconds::numeric / 3600.0, 1)                       AS watch_hours,
    first_sessions::bigint,
    -- Flags the transitions worth acting on, so the frontend does not have to
    -- encode product judgement in a colour scale.
    CASE
        WHEN exit_screen = 'paywall' THEN 'blocked'
        WHEN exit_screen = 'player'  THEN 'satisfied'
        WHEN exit_screen = 'search'  THEN 'unfulfilled search'
        WHEN exit_screen IN ('home', 'browse') AND mean_seconds < 120 THEN 'bounced'
        ELSE 'neutral'
    END                                                             AS exit_signal
FROM pairs
ORDER BY sessions DESC
