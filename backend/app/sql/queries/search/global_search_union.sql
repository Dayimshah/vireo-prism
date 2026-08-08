-- Global search across titles, users and sessions in one ranked result set.
--
-- Backs the command palette. A single UNION rather than three endpoints, so one
-- keystroke queries everything and the frontend has one loading state to manage.
--
-- Title matching uses the pg_trgm GIN index created in Alembic revision 0005. That
-- index is the reason `title ILIKE '%shadow%'` is fast: a btree cannot serve an
-- unanchored pattern, and without trigrams this would be a sequential scan of the
-- catalogue on every keystroke.
--
-- Numeric terms are treated as possible identifiers as well as text. Someone pasting
-- a user_id into a search box expects to find that user, and the `~ '^[0-9]+$'` guard
-- means a non-numeric term never reaches an integer comparison — which would
-- otherwise raise rather than simply not match.
WITH term AS (
    SELECT
        CAST(:query AS text)                                   AS raw,
        '%' || lower(trim(CAST(:query AS text))) || '%'        AS pattern,
        (trim(CAST(:query AS text)) ~ '^[0-9]+$')              AS is_numeric,
        CASE WHEN trim(CAST(:query AS text)) ~ '^[0-9]+$'
             THEN trim(CAST(:query AS text))::bigint
             ELSE NULL
        END                                            AS numeric_value
    FROM (SELECT 1) AS _
),
content_hits AS (
    SELECT
        'content'                                      AS result_type,
        c.content_id                                   AS result_id,
        c.title                                        AS label,
        g.name || ' · ' || c.content_type::text
            || ' · ' || c.release_year::text           AS sublabel,
        -- similarity() gives a graded score for near-misses, so a typo still ranks.
        -- An exact prefix match is boosted above it, because that is almost always
        -- what the user meant.
        CASE
            WHEN lower(c.title) = lower(trim(t.raw))           THEN 1.00
            WHEN lower(c.title) LIKE lower(trim(t.raw)) || '%' THEN 0.90
            ELSE LEAST(0.85, similarity(lower(c.title), lower(trim(t.raw))))
        END                                            AS score
    FROM core.content AS c
    JOIN core.genres  AS g ON g.genre_id = c.genre_id
    CROSS JOIN term AS t
    WHERE lower(c.title) LIKE t.pattern
       OR (t.is_numeric AND c.content_id = t.numeric_value)
),
user_hits AS (
    SELECT
        'user'                                         AS result_type,
        u.user_id                                      AS result_id,
        'User ' || u.user_id::text                     AS label,
        p.name || ' · ' || co.name
            || ' · joined ' || u.signup_date::text     AS sublabel,
        CASE WHEN t.is_numeric AND u.user_id = t.numeric_value THEN 1.00 ELSE 0.55 END
                                                       AS score
    FROM core.users     AS u
    JOIN core.personas  AS p  ON p.persona_id  = u.persona_id
    JOIN core.countries AS co ON co.country_id = u.country_id
    CROSS JOIN term AS t
    -- Users have no searchable name, so an id match is the only sensible lookup.
    -- Matching persona or country text here would return thousands of rows for a
    -- term like "india" and drown the useful results.
    WHERE t.is_numeric AND u.user_id = t.numeric_value
),
session_hits AS (
    SELECT
        'session'                                      AS result_type,
        s.session_id                                   AS result_id,
        'Session ' || s.session_id::text               AS label,
        'User ' || s.user_id::text
            || ' · ' || to_char(s.session_start, 'YYYY-MM-DD HH24:MI')
            || ' · ' || (s.duration_seconds / 60)::text || ' min'
                                                       AS sublabel,
        1.00                                           AS score
    FROM core.sessions AS s
    CROSS JOIN term AS t
    WHERE t.is_numeric AND s.session_id = t.numeric_value
),
genre_hits AS (
    SELECT
        'genre'                                        AS result_type,
        g.genre_id::bigint                             AS result_id,
        g.name                                         AS label,
        COUNT(c.content_id)::text || ' titles'         AS sublabel,
        CASE
            WHEN lower(g.name) = lower(trim(t.raw)) THEN 1.00
            ELSE 0.70
        END                                            AS score
    FROM core.genres AS g
    LEFT JOIN core.content AS c ON c.genre_id = g.genre_id
    CROSS JOIN term AS t
    WHERE lower(g.name) LIKE t.pattern
    GROUP BY g.genre_id, g.name, t.raw
)
SELECT result_type, result_id, label, sublabel, ROUND(score::numeric, 3) AS score
FROM (
    SELECT * FROM content_hits
    UNION ALL SELECT * FROM user_hits
    UNION ALL SELECT * FROM session_hits
    UNION ALL SELECT * FROM genre_hits
) AS combined
-- Below this, trigram matches are noise rather than near-misses.
WHERE score >= 0.10
ORDER BY score DESC, result_type, label
LIMIT CAST(:limit AS int)
