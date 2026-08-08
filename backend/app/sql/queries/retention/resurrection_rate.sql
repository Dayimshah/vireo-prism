-- Monthly resurrection: users who returned after 28+ days of dormancy.
--
-- Worth measuring separately from retention because it is the only positive signal
-- available about churned users, and because win-backs behave differently from
-- both new and continuing users. A rising resurrection rate alongside falling
-- retention usually means the product is losing people to a cadence problem rather
-- than a value problem — they keep coming back, just not on schedule.
--
-- The dormancy gap is computed with LAG over each user's active days, so
-- "resurrected" means a genuine break in their own timeline, not merely absence
-- from an arbitrary calendar window.
WITH activity AS (
    SELECT
        d.user_id,
        d.activity_date,
        LAG(d.activity_date) OVER (
            PARTITION BY d.user_id ORDER BY d.activity_date
        ) AS previous_active_date
    FROM analytics.mv_user_daily AS d
    JOIN core.users AS u USING (user_id)
    WHERE d.activity_date <= CAST(:date_to AS date)
      {{user_filter}}
),
events AS (
    SELECT
        date_trunc('month', activity_date)::date AS month,
        user_id,
        (activity_date - previous_active_date)   AS gap_days
    FROM activity
    WHERE previous_active_date IS NOT NULL
      AND activity_date >= CAST(:date_from AS date)
),
monthly AS (
    SELECT
        month,
        COUNT(DISTINCT user_id) FILTER (WHERE gap_days > 28)::bigint AS resurrected,
        COUNT(DISTINCT user_id)::bigint                              AS active_returning,
        ROUND(AVG(gap_days) FILTER (WHERE gap_days > 28)::numeric, 1) AS avg_dormant_days
    FROM events
    GROUP BY month
),
dormant_pool AS (
    -- Denominator: users who *were* dormant entering the month, and so were
    -- available to resurrect. Using total users instead would understate the rate
    -- by dividing by a population that was never at risk.
    SELECT
        m.month,
        COUNT(*)::bigint AS dormant_at_month_start
    FROM monthly AS m
    JOIN core.users AS u ON u.signup_date < m.month
    -- WHERE TRUE so the shared filter's leading AND composes cleanly. Cheaper than
    -- maintaining a second copy of the fragment without the leading conjunction.
    WHERE TRUE
      {{user_filter}}
      AND NOT EXISTS (
          SELECT 1 FROM analytics.mv_user_daily AS d
          WHERE d.user_id = u.user_id
            AND d.activity_date >= m.month - INTERVAL '28 days'
            AND d.activity_date < m.month
      )
    GROUP BY m.month
)
SELECT
    m.month,
    m.resurrected,
    COALESCE(p.dormant_at_month_start, 0)                            AS dormant_pool,
    m.active_returning,
    m.avg_dormant_days,
    ROUND(100.0 * m.resurrected / NULLIF(p.dormant_at_month_start, 0), 2)
                                                                     AS resurrection_rate_pct
FROM monthly AS m
LEFT JOIN dormant_pool AS p USING (month)
ORDER BY m.month
