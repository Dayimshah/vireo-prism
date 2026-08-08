-- Cross-device behaviour: how often users switch, and between which surfaces.
--
-- Two distinct measures, because they answer different questions.
--
-- The `transition` rows use LAG over each user's session history to find consecutive
-- sessions on different devices. That is directional and reveals migration patterns —
-- phone-to-TV is a user settling in for the evening, TV-to-phone is rarer and usually
-- means they left the room.
--
-- The `breadth` rows count how many distinct devices each user touches at all.
-- Multi-device users are the most valuable segment in streaming and the hardest to
-- see without this: they look like ordinary users on every single-device report.
WITH scoped AS (
    SELECT
        s.user_id,
        s.session_start,
        dv.form_factor,
        LAG(dv.form_factor) OVER (
            PARTITION BY s.user_id ORDER BY s.session_start
        ) AS previous_form_factor
    FROM core.sessions AS s
    JOIN core.users   AS u USING (user_id)
    JOIN core.devices AS dv ON dv.device_id = s.device_id
    WHERE s.session_start >= CAST(:date_from AS date)
      AND s.session_start < (CAST(:date_to AS date) + INTERVAL '1 day')
      {{user_filter}}
),
transitions AS (
    SELECT
        'transition'                                                   AS row_type,
        previous_form_factor || ' -> ' || form_factor                   AS label,
        COUNT(*)                                                       AS observations,
        COUNT(DISTINCT user_id)                                        AS users
    FROM scoped
    WHERE previous_form_factor IS NOT NULL
      AND previous_form_factor <> form_factor
    GROUP BY previous_form_factor, form_factor
),
-- Distinct device count per user, computed before bucketing. PostgreSQL will not
-- accept an aggregate inside GROUP BY, so the count has to be materialised in its
-- own step rather than bucketed inline.
device_counts AS (
    SELECT user_id, COUNT(DISTINCT form_factor) AS device_count
    FROM scoped
    GROUP BY user_id
),
breadth AS (
    SELECT
        'breadth'                                                      AS row_type,
        CASE
            WHEN device_count = 1 THEN '1 device'
            WHEN device_count = 2 THEN '2 devices'
            WHEN device_count = 3 THEN '3 devices'
            ELSE '4+ devices'
        END                                                            AS label,
        SUM(device_count)                                              AS observations,
        COUNT(*)                                                       AS users
    FROM device_counts
    GROUP BY 2
)
SELECT
    row_type,
    label,
    observations::bigint,
    users::bigint,
    ROUND(100.0 * users / SUM(users) OVER (PARTITION BY row_type), 2) AS pct_within_type
FROM (SELECT row_type, label, observations, users FROM transitions
      UNION ALL
      SELECT row_type, label, observations, users FROM breadth) AS combined
ORDER BY row_type, users DESC
