-- Genre affinity by persona: which archetypes watch which genres.
--
-- The second "planted signal recovered" query, and the most visually convincing of
-- them. seeder/personas.py declares that Anime Fans have a 9.5x affinity for Anime
-- and Sports Fans a 7.8x affinity for Sports. This query reads neither that file nor
-- core.personas' coefficients — it counts watch time in the event stream and groups
-- by two foreign keys. The strong diagonal it produces was recovered, not asserted.
--
-- The measure is *share of a persona's own watch time*, not absolute hours. Absolute
-- hours would simply rank personas by how much they watch overall and wash out the
-- taste signal entirely: Binge Watchers would dominate every genre column.
--
-- The lift column is what makes the heatmap readable. It compares each persona's
-- share of a genre against the population's share, so 2.0 means "this persona
-- watches twice as much of this genre as the average user does". A raw share
-- already looks high for large genres regardless of affinity.
-- Pre-aggregate to (user, title) before joining the dimensions.
--
-- The natural phrasing joins users, personas, content and genres at event level, then
-- groups. That works and measured 5.4s: it carries a million playback rows through
-- four joins before collapsing them to 128 output cells. Aggregating first reduces the
-- join input by roughly twentyfold for identical results.
--
-- The event_name restriction is explicit rather than relying on `watch_seconds IS NOT
-- NULL`. Semantically equivalent — revision 0004's
-- ck_events_watch_only_on_playback guarantees it — but naming the values lets the
-- planner use ix_events_name_time instead of filtering post-scan.
WITH playback AS (
    SELECT
        e.user_id,
        e.content_id,
        SUM(e.watch_seconds)  AS watch_seconds,
        COUNT(*)              AS playback_events
    FROM core.events AS e
    WHERE e.event_name IN ('VIDEO_PROGRESS', 'PAUSE_VIDEO', 'ABANDON_VIDEO', 'COMPLETE_VIDEO')
      AND e.content_id IS NOT NULL
      AND e.watch_seconds > 0
      AND e.event_time >= CAST(:date_from AS date)
      AND e.event_time < (CAST(:date_to AS date) + INTERVAL '1 day')
    GROUP BY e.user_id, e.content_id
),
persona_genre AS (
    SELECT
        p.name                        AS persona,
        g.name                        AS genre,
        SUM(pb.watch_seconds)         AS watch_seconds,
        SUM(pb.playback_events)       AS playback_events
    FROM playback AS pb
    JOIN core.users    AS u ON u.user_id    = pb.user_id
    JOIN core.personas AS p ON p.persona_id = u.persona_id
    JOIN core.content  AS c ON c.content_id = pb.content_id
    JOIN core.genres   AS g ON g.genre_id   = c.genre_id
    WHERE TRUE
      {{user_filter}}
      {{content_filter}}
    GROUP BY p.name, g.name
),
population AS (
    -- Baseline: each genre's share of all watch time, across every persona.
    SELECT
        genre,
        SUM(watch_seconds)::numeric
            / NULLIF(SUM(SUM(watch_seconds)) OVER (), 0) AS population_share
    FROM persona_genre
    GROUP BY genre
)
SELECT
    pg.persona,
    pg.genre,
    pg.watch_seconds::bigint,
    ROUND(pg.watch_seconds::numeric / 3600.0, 1)                     AS watch_hours,
    pg.playback_events::bigint,
    -- This persona's watch time in this genre as a share of their own total.
    ROUND(
        100.0 * pg.watch_seconds
        / NULLIF(SUM(pg.watch_seconds) OVER (PARTITION BY pg.persona), 0), 2
    )                                                                AS pct_of_persona_watch,
    ROUND(100.0 * pop.population_share, 2)                           AS pct_of_all_watch,
    -- Affinity lift. >1 means over-indexed relative to the population.
    ROUND(
        (pg.watch_seconds::numeric
         / NULLIF(SUM(pg.watch_seconds) OVER (PARTITION BY pg.persona), 0))
        / NULLIF(pop.population_share, 0), 2
    )                                                                AS affinity_lift,
    -- Rank within the persona, so the frontend can label each row's top genres
    -- without a second pass.
    RANK() OVER (PARTITION BY pg.persona ORDER BY pg.watch_seconds DESC)
                                                                     AS rank_within_persona
FROM persona_genre AS pg
JOIN population AS pop USING (genre)
ORDER BY pg.persona, pg.watch_seconds DESC
