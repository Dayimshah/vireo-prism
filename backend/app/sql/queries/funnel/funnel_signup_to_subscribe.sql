-- Signup-to-subscription funnel, user-scoped over each user's whole lifetime.
--
-- Distinct from the discovery funnel in an important way: that one is session-scoped
-- and answers "what happened in this visit", while this is user-scoped and answers
-- "how far did this person ever get". Mixing the two grains is the most common error
-- in funnel analysis — a user who browsed on Monday and subscribed on Friday
-- converted, even though no single session contains both steps.
--
-- The steps are cumulative, so each requires all prior ones. That is why
-- `activated` is defined as having completed something rather than merely started:
-- activation should mean deriving value, and a start that was abandoned did not.
WITH cohort AS (
    SELECT
        u.user_id,
        u.signup_date
    FROM core.users AS u
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
milestones AS (
    SELECT
        c.user_id,
        EXISTS (
            SELECT 1 FROM analytics.mv_user_daily AS d
            WHERE d.user_id = c.user_id
        ) AS opened_app,
        EXISTS (
            SELECT 1 FROM analytics.mv_user_daily AS d
            WHERE d.user_id = c.user_id AND d.started_videos > 0
        ) AS started_content,
        EXISTS (
            SELECT 1 FROM analytics.mv_user_daily AS d
            WHERE d.user_id = c.user_id AND d.completed_videos > 0
        ) AS completed_content,
        EXISTS (
            SELECT 1 FROM analytics.mv_user_daily AS d
            WHERE d.user_id = c.user_id AND d.subscribe_clicks > 0
        ) AS saw_paywall,
        EXISTS (
            SELECT 1 FROM core.subscriptions AS s
            WHERE s.user_id = c.user_id AND s.status = 'trialing'
        ) AS started_trial,
        EXISTS (
            SELECT 1 FROM core.subscriptions AS s
            WHERE s.user_id = c.user_id AND s.mrr_usd > 0
        ) AS subscribed_paid,
        EXISTS (
            SELECT 1 FROM core.subscriptions AS s
            WHERE s.user_id = c.user_id AND s.mrr_usd > 0 AND s.ended_on IS NULL
        ) AS still_subscribed
    FROM cohort AS c
),
steps AS (
    SELECT
        COUNT(*)                                                        AS s1_signed_up,
        COUNT(*) FILTER (WHERE opened_app)                              AS s2_opened,
        COUNT(*) FILTER (WHERE opened_app AND started_content)          AS s3_started,
        COUNT(*) FILTER (WHERE opened_app AND started_content
                           AND completed_content)                       AS s4_activated,
        COUNT(*) FILTER (WHERE opened_app AND started_content
                           AND completed_content AND saw_paywall)       AS s5_paywall,
        -- Not gated on saw_paywall: a user can subscribe from a settings page or an
        -- email link without ever hitting the in-app paywall, and forcing that
        -- dependency would understate conversion.
        COUNT(*) FILTER (WHERE opened_app AND started_content
                           AND completed_content
                           AND (started_trial OR subscribed_paid))      AS s6_trial_or_paid,
        COUNT(*) FILTER (WHERE opened_app AND started_content
                           AND completed_content AND subscribed_paid)   AS s7_paid,
        COUNT(*) FILTER (WHERE opened_app AND started_content
                           AND completed_content AND still_subscribed)  AS s8_retained_paid
    FROM milestones
),
long AS (
    SELECT 1 AS step_order, 'Signed up'                 AS step_name, s1_signed_up     AS users FROM steps
    UNION ALL SELECT 2, 'Opened the app',                s2_opened        FROM steps
    UNION ALL SELECT 3, 'Started something',             s3_started       FROM steps
    UNION ALL SELECT 4, 'Completed something',           s4_activated     FROM steps
    UNION ALL SELECT 5, 'Reached the paywall',           s5_paywall       FROM steps
    UNION ALL SELECT 6, 'Started trial or subscribed',   s6_trial_or_paid FROM steps
    UNION ALL SELECT 7, 'Paid',                          s7_paid          FROM steps
    UNION ALL SELECT 8, 'Still paying',                  s8_retained_paid FROM steps
)
SELECT
    step_order,
    step_name,
    users::bigint,
    ROUND(100.0 * users
          / NULLIF(FIRST_VALUE(users) OVER (ORDER BY step_order), 0), 2) AS pct_of_signups,
    ROUND(100.0 * users
          / NULLIF(LAG(users) OVER (ORDER BY step_order), 0), 2)         AS pct_of_previous,
    (LAG(users) OVER (ORDER BY step_order) - users)::bigint              AS dropped_from_previous
FROM long
ORDER BY step_order
