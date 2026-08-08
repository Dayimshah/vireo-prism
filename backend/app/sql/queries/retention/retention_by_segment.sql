-- Day-N retention split by a caller-chosen dimension.
--
-- The segment dimension arrives as a bound :segment_by parameter resolved through
-- a CASE, not as an interpolated column name. That is the injection-safe way to
-- offer a dynamic GROUP BY: PostgreSQL will not accept a parameter in a GROUP BY
-- position, and building the clause with string formatting would put a
-- request-derived value into query text. The allowlist is the CASE arms
-- themselves — an unrecognised value yields 'all', not an error and not a
-- surprise.
WITH cohorts AS (
    SELECT
        u.user_id,
        u.signup_date,
        CASE CAST(:segment_by AS text)
            WHEN 'country'  THEN co.name
            WHEN 'channel'  THEN ch.name
            WHEN 'persona'  THEN p.name
            WHEN 'device'   THEN dv.form_factor
            WHEN 'premium'  THEN CASE WHEN u.is_premium THEN 'premium' ELSE 'free' END
            ELSE 'all'
        END AS segment
    FROM core.users AS u
    JOIN core.countries          AS co ON co.country_id = u.country_id
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    JOIN core.personas           AS p  ON p.persona_id  = u.persona_id
    JOIN core.devices            AS dv ON dv.device_id  = u.device_id
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
milestones AS (
    SELECT unnest(ARRAY[1, 7, 28]) AS day_n
),
eligible AS (
    SELECT c.segment, m.day_n, COUNT(*)::bigint AS cohort_size
    FROM cohorts AS c
    CROSS JOIN milestones AS m
    WHERE c.signup_date + m.day_n <= CAST(:observation_end AS date)
    GROUP BY c.segment, m.day_n
),
retained AS (
    SELECT
        c.segment,
        m.day_n,
        COUNT(DISTINCT d.user_id)::bigint AS retained_users
    FROM cohorts AS c
    CROSS JOIN milestones AS m
    JOIN analytics.mv_user_daily AS d
      ON d.user_id = c.user_id
     AND d.days_since_signup = m.day_n
    WHERE c.signup_date + m.day_n <= CAST(:observation_end AS date)
    GROUP BY c.segment, m.day_n
)
SELECT
    e.segment,
    e.day_n,
    e.cohort_size,
    COALESCE(r.retained_users, 0)                                     AS retained_users,
    ROUND(100.0 * COALESCE(r.retained_users, 0) / NULLIF(e.cohort_size, 0), 2)
                                                                      AS retention_pct
FROM eligible AS e
LEFT JOIN retained AS r USING (segment, day_n)
-- Small cohorts produce retention figures that swing wildly on one or two users.
-- Suppressing them is more honest than plotting noise as signal.
WHERE e.cohort_size >= CAST(:min_cohort_size AS int)
ORDER BY e.segment, e.day_n
