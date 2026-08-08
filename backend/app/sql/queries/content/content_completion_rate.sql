-- Completion rate per title, with the abandonment point.
--
-- The ratio is computed as SUM(completions) / SUM(starts) over the window, never as
-- an average of per-day rates. Averaging stored ratios is Simpson's paradox waiting
-- to happen: a title with 1 start and 1 completion on Monday and 200 starts with 40
-- completions on Tuesday averages to 60%, while its true rate is 20%. That is why
-- analytics.mv_content_daily stores numerator and denominator separately rather
-- than a precomputed rate.
--
-- avg_abandon_pct is the column that makes this actionable. A title abandoned at 12%
-- has a hook problem; one abandoned at 78% has an ending problem. The completion
-- rate alone cannot distinguish them.
SELECT
    c.content_id,
    c.title,
    g.name                                                    AS genre,
    c.content_type::text                                      AS content_type,
    c.runtime_minutes,
    c.is_original,
    ROUND(c.popularity_score, 1)                              AS popularity_score,
    SUM(d.starts)::bigint                                     AS starts,
    SUM(d.completions)::bigint                                AS completions,
    SUM(d.abandons)::bigint                                   AS abandons,
    SUM(d.unique_viewers)::bigint                             AS viewer_days,
    ROUND(100.0 * SUM(d.completions) / NULLIF(SUM(d.starts), 0), 2)
                                                              AS completion_rate_pct,
    -- Weighted by abandonment count, so a title with one early quitter and fifty
    -- late ones reports the late figure.
    ROUND(
        SUM(d.avg_abandon_pct * d.abandons) / NULLIF(SUM(d.abandons), 0), 1
    )                                                         AS avg_abandon_pct,
    ROUND(SUM(d.watch_seconds)::numeric / 3600.0, 1)          AS watch_hours,
    ROUND(AVG(d.avg_rating)::numeric, 2)                      AS avg_rating
FROM analytics.mv_content_daily AS d
JOIN core.content AS c USING (content_id)
JOIN core.genres  AS g ON g.genre_id = c.genre_id
WHERE d.activity_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
  {{content_filter}}
GROUP BY c.content_id, c.title, g.name, c.content_type, c.runtime_minutes,
         c.is_original, c.popularity_score
-- Titles with a handful of starts produce completion rates of 0% or 100% that mean
-- nothing. Suppressing them keeps the leaderboard honest.
HAVING SUM(d.starts) >= CAST(:min_starts AS int)
ORDER BY completion_rate_pct DESC NULLS LAST, starts DESC
LIMIT CAST(:limit AS int)
