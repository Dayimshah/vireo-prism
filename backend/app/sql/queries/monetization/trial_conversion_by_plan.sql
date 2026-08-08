-- Trial-to-paid conversion by plan, with time to convert.
--
-- Answers whether the trial is doing its job, and whether that differs by price
-- point. The expected pattern is that cheaper plans convert at a higher rate — less
-- to justify — while expensive plans contribute more revenue per conversion. Which
-- of those a business should optimise depends on its margin structure, so both are
-- reported rather than reduced to one score.
--
-- The trial-to-paid link is reconstructed by matching a converted trial against the
-- paid subscription that followed it. The seeder closes a converting trial the day
-- before the paid term opens, so `is_trial_conversion` on the paid row is the
-- authoritative marker — but the window join below also verifies the timing, which is
-- what catches a generator bug rather than trusting a flag.
WITH trials AS (
    SELECT
        s.subscription_id,
        s.user_id,
        s.plan_id,
        s.started_on,
        s.ended_on,
        s.status,
        s.cancel_reason
    FROM core.subscriptions AS s
    JOIN core.users AS u USING (user_id)
    WHERE s.status IN ('trialing', 'cancelled', 'expired')
      AND s.mrr_usd = 0                      -- trials earn nothing by definition
      AND s.started_on BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)
      {{user_filter}}
),
conversions AS (
    SELECT
        t.subscription_id                    AS trial_id,
        t.plan_id                            AS trial_plan_id,
        t.started_on                         AS trial_started_on,
        paid.plan_id                         AS paid_plan_id,
        paid.started_on                      AS paid_started_on,
        paid.mrr_usd,
        paid.billing_period,
        paid.ended_on                        AS paid_ended_on,
        (paid.started_on - t.started_on)     AS days_to_convert
    FROM trials AS t
    LEFT JOIN core.subscriptions AS paid
           ON paid.user_id = t.user_id
          AND paid.mrr_usd > 0
          AND paid.is_trial_conversion
          -- Must begin during or immediately after the trial. A paid subscription
          -- months later is a separate decision, not a trial conversion.
          AND paid.started_on >= t.started_on
          AND paid.started_on <= COALESCE(t.ended_on, t.started_on) + 1
)
SELECT
    pl.name                                                          AS trial_plan,
    pl.tier                                                          AS plan_tier,
    ROUND(pl.monthly_price_usd, 2)                                   AS list_price_usd,
    COUNT(*)::bigint                                                 AS trials_started,
    COUNT(c.paid_started_on)::bigint                                 AS trials_converted,
    ROUND(100.0 * COUNT(c.paid_started_on) / NULLIF(COUNT(*), 0), 2) AS conversion_pct,
    ROUND(AVG(c.days_to_convert)::numeric, 1)                        AS avg_days_to_convert,
    ROUND(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY c.days_to_convert)::numeric, 1
    )                                                                AS median_days_to_convert,
    -- Converting to a *different* plan than the trial is a signal the trial plan is
    -- mispriced or misframed for the audience it attracts.
    COUNT(*) FILTER (WHERE c.paid_plan_id IS NOT NULL
                       AND c.paid_plan_id <> c.trial_plan_id)::bigint AS switched_plan,
    ROUND(AVG(c.mrr_usd), 2)                                         AS avg_converted_mrr_usd,
    ROUND(SUM(c.mrr_usd), 2)                                         AS total_converted_mrr_usd,
    COUNT(*) FILTER (WHERE c.billing_period = 'annual')::bigint      AS chose_annual,
    -- Survival of converted subscriptions: a high trial conversion that churns
    -- immediately is a trial that oversold, not a trial that worked.
    COUNT(*) FILTER (WHERE c.paid_started_on IS NOT NULL
                       AND c.paid_ended_on IS NULL)::bigint          AS still_paying,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE c.paid_started_on IS NOT NULL
                                   AND c.paid_ended_on IS NULL)
        / NULLIF(COUNT(c.paid_started_on), 0), 1
    )                                                                AS post_conversion_retention_pct
FROM conversions AS c
JOIN core.subscription_plans AS pl ON pl.plan_id = c.trial_plan_id
GROUP BY pl.name, pl.tier, pl.monthly_price_usd
HAVING COUNT(*) >= CAST(:min_cohort_size AS int)
ORDER BY conversion_pct DESC
