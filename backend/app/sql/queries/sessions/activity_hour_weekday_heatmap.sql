-- Session volume by hour of day and day of week.
--
-- A note on what this chart honestly shows, because it is the one place where the
-- generator's timezone handling becomes visible:
--
-- Sessions are generated in each user's *local* evening and stored as UTC. Reading
-- hour-of-day in UTC therefore shows a broad plateau rather than a single sharp
-- spike, because 21:00 in Mumbai and 21:00 in São Paulo are eight hours apart. That
-- smearing is correct. A synthetic dataset that showed the entire world watching at
-- the same UTC instant would be the tell that it was generated naively.
--
-- Both columns are returned so the dashboard can offer either view. The local-time
-- column applies each country's fixed offset, which is what a real warehouse would
-- do with a timezone dimension.
SELECT
    EXTRACT(DOW  FROM s.session_start AT TIME ZONE 'UTC')::int   AS weekday_utc,
    EXTRACT(HOUR FROM s.session_start AT TIME ZONE 'UTC')::int   AS hour_utc,
    EXTRACT(HOUR FROM s.session_start
            + make_interval(mins => (co.utc_offset_minutes)))::int AS hour_local,
    COUNT(*)::bigint                                             AS sessions,
    COUNT(DISTINCT s.user_id)::bigint                            AS unique_users,
    ROUND(AVG(s.duration_seconds)::numeric / 60.0, 1)            AS avg_duration_minutes,
    SUM(s.watch_seconds)::bigint                                 AS watch_seconds
FROM core.sessions AS s
JOIN core.users AS u USING (user_id)
JOIN (
    -- Offsets mirror seeder/seasonality.py. Fixed rather than IANA zones on
    -- purpose: a DST transition would shift an hour of history between two
    -- machines with different tzdata versions, breaking reproducibility for a
    -- fidelity nobody is measuring.
    SELECT country_id,
           CASE iso_code
               WHEN 'IN' THEN 330  WHEN 'US' THEN -360 WHEN 'GB' THEN 0
               WHEN 'CA' THEN -300 WHEN 'AU' THEN 600  WHEN 'DE' THEN 60
               WHEN 'FR' THEN 60   WHEN 'JP' THEN 540  WHEN 'KR' THEN 540
               WHEN 'SG' THEN 480  WHEN 'AE' THEN 240  WHEN 'BR' THEN -180
               WHEN 'MX' THEN -360 WHEN 'ES' THEN 60   WHEN 'IT' THEN 60
               WHEN 'NL' THEN 60   WHEN 'ZA' THEN 120  WHEN 'ID' THEN 420
               WHEN 'PH' THEN 480  WHEN 'NG' THEN 60
               ELSE 0
           END AS utc_offset_minutes
    FROM core.countries
) AS co ON co.country_id = u.country_id
WHERE s.session_start >= CAST(:date_from AS date)
  AND s.session_start < (CAST(:date_to AS date) + INTERVAL '1 day')
  {{user_filter}}
GROUP BY weekday_utc, hour_utc, hour_local
ORDER BY weekday_utc, hour_utc
