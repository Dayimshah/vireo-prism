-- Channel scorecard: acquisition volume through to retained revenue.
--
-- One row per channel covering the whole journey, so the Marketing page has a single
-- table behind it. Deliberately overlaps with cohort_ltv_by_channel on revenue but
-- differs in emphasis: that query answers "what is a user from this channel worth",
-- this one answers "what did this channel actually deliver, and did those users stay".
--
-- The engagement columns are the ones that expose channel quality early. Conversion
-- and LTV take months to become reliable, whereas mean sessions and completion rate
-- in the first weeks separate a channel bringing interested users from one buying
-- clicks — which is a decision you need before the LTV data exists.
WITH cohort AS (
    SELECT
        u.user_id,
        u.signup_date,
        u.is_premium,
        u.churned_at,
        ch.name          AS channel,
        ch.channel_group,
        ch.is_paid,
        ch.cac_usd
    FROM core.users AS u
    JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
    WHERE u.signup_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
engagement AS (
    SELECT
        c.user_id,
        l.total_sessions,
        l.total_watch_seconds,
        l.completion_rate,
        l.active_days,
        l.distinct_content,
        l.lifetime_revenue_usd,
        l.current_mrr_usd,
        l.days_since_last_active
    FROM cohort AS c
    LEFT JOIN analytics.mv_user_lifetime AS l USING (user_id)
)
SELECT
    c.channel,
    c.channel_group,
    c.is_paid,
    ROUND(c.cac_usd, 2)                                              AS cac_usd,
    COUNT(*)::bigint                                                 AS users_acquired,
    -- Users who never generated a single event: the clearest signal of a channel
    -- delivering clicks rather than people.
    COUNT(*) FILTER (WHERE e.total_sessions IS NULL)::bigint         AS never_activated,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE e.total_sessions IS NULL) / NULLIF(COUNT(*), 0), 1
    )                                                                AS never_activated_pct,
    ROUND(AVG(e.total_sessions)::numeric, 1)                         AS avg_sessions,
    ROUND(AVG(e.total_watch_seconds)::numeric / 3600.0, 1)           AS avg_watch_hours,
    ROUND(AVG(e.completion_rate)::numeric, 3)                        AS avg_completion_rate,
    ROUND(AVG(e.distinct_content)::numeric, 1)                       AS avg_titles_watched,
    COUNT(*) FILTER (WHERE e.lifetime_revenue_usd > 0)::bigint       AS converted_users,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE e.lifetime_revenue_usd > 0) / NULLIF(COUNT(*), 0), 2
    )                                                                AS conversion_pct,
    COUNT(*) FILTER (WHERE c.churned_at IS NOT NULL)::bigint         AS churned_users,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE c.churned_at IS NOT NULL) / NULLIF(COUNT(*), 0), 2
    )                                                                AS churn_pct,
    ROUND(SUM(COALESCE(e.lifetime_revenue_usd, 0)), 2)               AS total_revenue_usd,
    ROUND(SUM(COALESCE(e.current_mrr_usd, 0)), 2)                    AS current_mrr_usd,
    ROUND(c.cac_usd * COUNT(*), 2)                                   AS total_spend_usd,
    ROUND(
        SUM(COALESCE(e.lifetime_revenue_usd, 0)) - c.cac_usd * COUNT(*), 2
    )                                                                AS net_contribution_usd,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)               AS share_of_users_pct,
    ROUND(
        100.0 * SUM(COALESCE(e.lifetime_revenue_usd, 0))
        / NULLIF(SUM(SUM(COALESCE(e.lifetime_revenue_usd, 0))) OVER (), 0), 2
    )                                                                AS share_of_revenue_pct
FROM cohort AS c
LEFT JOIN engagement AS e USING (user_id)
GROUP BY c.channel, c.channel_group, c.is_paid, c.cac_usd
HAVING COUNT(*) >= CAST(:min_cohort_size AS int)
ORDER BY net_contribution_usd DESC
