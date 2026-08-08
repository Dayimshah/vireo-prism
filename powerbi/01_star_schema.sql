-- Star-schema views for Power BI.
--
-- Run once against a seeded database:
--
--     make powerbi
--     -- or --
--     docker compose exec -T postgres sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < powerbi/01_star_schema.sql
--
-- Why a separate schema rather than an Alembic migration
-- -----------------------------------------------------
-- This is deliberately NOT a migration. `core` and `analytics` are the schema the
-- API, the 51 delivered queries and the 330-test suite were all verified against,
-- and adding tables to them for the benefit of a BI tool would change the surface
-- every one of those depends on. Everything here lives in its own `powerbi`
-- schema, is derived entirely from what already exists, and is removable with one
-- statement:
--
--     DROP SCHEMA powerbi CASCADE;
--
-- Nothing else in the project reads it. It is safe to skip entirely.
--
-- Why views and not tables
-- ------------------------
-- Power BI imports on refresh, so the extra copy a materialized view would keep is
-- storage for nothing — the model already caches its own snapshot. Plain views
-- also mean a `make refresh` of the analytics layer is immediately visible to the
-- next Power BI refresh with no second rebuild step to forget.
--
-- The exception is `dim_date`, which is a table. It is generated arithmetic rather
-- than a projection of anything, and Power BI wants a contiguous date spine it can
-- mark as a date table.
--
-- Grain and naming
-- ----------------
-- `dim_*` is one row per entity, `fact_*` is one row per event-grain observation.
-- Every fact carries surrogate keys only; every label lives on a dimension. That
-- is what lets Power BI build a single-direction star and avoids the ambiguous
-- filter paths a snowflake produces.

BEGIN;

DROP SCHEMA IF EXISTS powerbi CASCADE;
CREATE SCHEMA powerbi;

COMMENT ON SCHEMA powerbi IS
    'Star-schema projection of core/analytics for Power BI. Derived and disposable; '
    'nothing in the API or dashboard reads it. Rebuild with powerbi/01_star_schema.sql.';


-- ===========================================================================
-- dim_date
--
-- A table, not a view. Power BI needs a contiguous date spine with no gaps to
-- mark as its date table; a view over observed activity dates would omit any day
-- nobody was active, and time intelligence (same-period-last-year, running
-- totals) silently miscomputes across a gap rather than failing.
--
-- Bounds come from the data, padded to whole years so a fiscal-year or
-- year-over-year visual has complete endpoints to work with.
-- ===========================================================================
CREATE TABLE powerbi.dim_date AS
WITH bounds AS (
    SELECT
        DATE_TRUNC('year', MIN(activity_date))::date                        AS first_day,
        (DATE_TRUNC('year', MAX(activity_date)) + INTERVAL '1 year - 1 day')::date
                                                                           AS last_day
    FROM analytics.mv_user_daily
)
SELECT
    d::date                                                    AS date_key,
    EXTRACT(year    FROM d)::smallint                          AS year,
    EXTRACT(quarter FROM d)::smallint                          AS quarter,
    EXTRACT(month   FROM d)::smallint                          AS month_number,
    TO_CHAR(d, 'Mon')                                          AS month_short,
    -- TRIM because TO_CHAR's 'Month' is blank-padded to nine characters, which
    -- reaches a Power BI slicer as "May      " and sorts and filters as such.
    TRIM(TO_CHAR(d, 'Month'))                                  AS month_name,
    TO_CHAR(d, 'YYYY-MM')                                      AS year_month,
    EXTRACT(week    FROM d)::smallint                          AS iso_week,
    EXTRACT(isoyear FROM d)::int                               AS iso_year,
    EXTRACT(isodow  FROM d)::smallint                          AS iso_weekday,
    TRIM(TO_CHAR(d, 'Dy'))                                     AS weekday_short,
    (EXTRACT(isodow FROM d) >= 6)                              AS is_weekend,
    DATE_TRUNC('week',  d)::date                               AS week_start,
    DATE_TRUNC('month', d)::date                               AS month_start,
    DATE_TRUNC('quarter', d)::date                             AS quarter_start,
    -- Sort keys. Power BI sorts text alphabetically unless told otherwise, which
    -- puts April before January on every axis. These are the "Sort by column"
    -- targets, and forgetting them is the most common Power BI modelling error.
    (EXTRACT(year FROM d) * 100 + EXTRACT(month FROM d))::int  AS year_month_sort,
    -- isoyear, NOT year. ISO week 1 of 2026 begins on 2025-12-29, so pairing the
    -- calendar year with the ISO week gives that day 202501 — which sorts before
    -- 2025's week 52 and puts the year boundary in the wrong place on every weekly
    -- axis. EXTRACT(isoyear) returns 2026 for that date, which is the whole point
    -- of the ISO week-numbering year.
    (EXTRACT(isoyear FROM d) * 100 + EXTRACT(week FROM d))::int AS year_week_sort,
    -- The matching label, so the axis text agrees with the sort key.
    TO_CHAR(d, 'IYYY-"W"IW')                                   AS iso_year_week
FROM bounds, GENERATE_SERIES(bounds.first_day, bounds.last_day, INTERVAL '1 day') AS g(d);

ALTER TABLE powerbi.dim_date ADD PRIMARY KEY (date_key);

COMMENT ON TABLE powerbi.dim_date IS
    'Contiguous date spine padded to whole years. Mark as the date table in Power BI '
    'and set year_month_sort / year_week_sort as the sort-by columns.';


-- ===========================================================================
-- Conformed dimensions
--
-- Thin projections. The point of restating them rather than pointing Power BI at
-- `core` directly is that a BI model wants stable, self-describing column names
-- and no foreign keys it has to be told to ignore.
-- ===========================================================================

CREATE VIEW powerbi.dim_country AS
SELECT
    country_id                        AS country_key,
    iso_code,
    name                              AS country,
    region,
    tier                              AS country_tier,
    'Tier ' || tier::text             AS country_tier_label
FROM core.countries;

CREATE VIEW powerbi.dim_channel AS
SELECT
    channel_id                        AS channel_key,
    name                              AS channel,
    channel_group,
    is_paid,
    CASE WHEN is_paid THEN 'Paid' ELSE 'Organic' END AS paid_label,
    cac_usd
FROM core.marketing_channels;

CREATE VIEW powerbi.dim_persona AS
SELECT
    persona_id                        AS persona_key,
    name                              AS persona,
    description                       AS persona_description,
    base_sessions_per_week,
    base_completion_rate,
    base_churn_propensity
FROM core.personas;

CREATE VIEW powerbi.dim_device AS
SELECT
    device_id                         AS device_key,
    name                              AS device,
    platform,
    form_factor
FROM core.devices;

CREATE VIEW powerbi.dim_genre AS
SELECT
    genre_id                          AS genre_key,
    name                              AS genre
FROM core.genres;

CREATE VIEW powerbi.dim_plan AS
SELECT
    plan_id                           AS plan_key,
    name                              AS plan,
    tier                              AS plan_tier,
    monthly_price_usd,
    max_streams,
    has_ads
FROM core.subscription_plans;


-- ===========================================================================
-- dim_user
--
-- One row per user, carrying the lifetime aggregates from
-- analytics.mv_user_lifetime so that user-level analysis needs no fact table.
-- Retains raw foreign keys as `*_key` so Power BI can relate to the conformed
-- dimensions above.
--
-- Note `persona` yields seven distinct values, not eight: New Explorer is a
-- transient state that graduates after 30 days, so no stored user carries it.
-- See docs/seeder-design.md.
-- ===========================================================================
CREATE VIEW powerbi.dim_user AS
SELECT
    u.user_id                                        AS user_key,
    u.signup_date,
    u.country_id                                     AS country_key,
    u.channel_id                                     AS channel_key,
    u.persona_id                                     AS persona_key,
    u.device_id                                      AS device_key,
    u.is_premium,
    u.age,
    u.gender,
    u.app_version,
    -- Age bands, because a Power BI reader wants a categorical axis and computing
    -- one in DAX per visual is both slower and easier to get inconsistent.
    CASE
        WHEN u.age < 18 THEN 'Under 18'
        WHEN u.age < 25 THEN '18-24'
        WHEN u.age < 35 THEN '25-34'
        WHEN u.age < 45 THEN '35-44'
        WHEN u.age < 55 THEN '45-54'
        ELSE '55+'
    END                                              AS age_band,
    u.last_seen_at,
    u.churned_at,
    (u.churned_at IS NOT NULL)                       AS has_churned,
    l.first_active_date,
    l.last_active_date,
    l.active_days,
    l.total_sessions,
    l.total_events,
    l.total_watch_seconds,
    ROUND(l.total_watch_seconds / 3600.0, 2)         AS total_watch_hours,
    l.started_videos,
    l.completed_videos,
    l.abandoned_videos,
    -- 0-1 fraction in the source. Restated as percentage points here because a
    -- Power BI model mixing both conventions is a factor-of-100 bug waiting to
    -- happen; see the percentage note in docs/powerbi.md.
    ROUND(l.completion_rate * 100, 2)                AS completion_rate_pct,
    l.distinct_content,
    l.distinct_genres,
    l.distinct_devices,
    l.subscription_count,
    l.first_subscribed_on,
    l.has_active_subscription,
    l.ever_converted_trial,
    l.current_mrr_usd,
    l.lifetime_revenue_usd,
    l.tenure_days,
    l.days_since_last_active,
    l.watch_seconds_28d,
    l.active_days_28d
FROM core.users AS u
LEFT JOIN analytics.mv_user_lifetime AS l USING (user_id);


-- ===========================================================================
-- dim_content
-- ===========================================================================
CREATE VIEW powerbi.dim_content AS
SELECT
    content_id                        AS content_key,
    title,
    genre_id                          AS genre_key,
    content_type::text                AS content_type,
    runtime_minutes,
    release_year,
    language,
    age_rating,
    popularity_score,
    season_count,
    episode_count,
    is_original,
    added_on,
    -- Films have NULL season/episode counts; that is meaningful absence, not zero.
    -- This flag lets a visual split the two without a blank-handling measure.
    (season_count IS NOT NULL)        AS is_episodic
FROM core.content;


-- ===========================================================================
-- fact_user_daily
--
-- Grain: one row per user per active day. The spine for DAU/WAU/MAU, retention
-- and engagement. Days with no activity are absent by construction — that is
-- what makes a null distinguishable from a measured zero, and why the DAX
-- measures in 02_measures.dax count over dim_date rather than over this fact.
-- ===========================================================================
CREATE VIEW powerbi.fact_user_daily AS
SELECT
    d.user_id                         AS user_key,
    d.activity_date                   AS date_key,
    d.signup_date,
    d.days_since_signup,
    d.is_signup_day,
    d.sessions,
    d.events,
    d.watch_seconds,
    ROUND(d.watch_seconds / 3600.0, 4) AS watch_hours,
    d.started_videos,
    d.completed_videos,
    d.abandoned_videos,
    d.searches,
    d.subscribe_clicks,
    d.distinct_content
FROM analytics.mv_user_daily AS d;


-- ===========================================================================
-- fact_content_daily
--
-- Grain: one row per title per day with activity.
-- ===========================================================================
CREATE VIEW powerbi.fact_content_daily AS
SELECT
    c.content_id                      AS content_key,
    c.activity_date                   AS date_key,
    c.unique_viewers,
    c.detail_views,
    c.trailer_views,
    c.starts,
    c.completions,
    c.abandons,
    c.watchlist_adds,
    c.ratings,
    c.watch_seconds,
    ROUND(c.watch_seconds / 3600.0, 4) AS watch_hours,
    c.avg_rating,
    c.avg_abandon_pct
FROM analytics.mv_content_daily AS c;


-- ===========================================================================
-- fact_session_funnel
--
-- Grain: one row per session. Twelve step booleans plus first-touch timestamps,
-- so funnel and time-between-steps analysis is a filter and a subtraction rather
-- than a self-join over 1.1M events.
--
-- The booleans are restated as 1/0 integers alongside the boolean form: Power BI
-- can average a boolean, but a reader building a conversion measure will reach
-- for SUM, and an integer column makes that work as expected.
-- ===========================================================================
CREATE VIEW powerbi.fact_session_funnel AS
SELECT
    f.session_id                      AS session_key,
    f.user_id                         AS user_key,
    f.session_date                    AS date_key,
    s.device_id                       AS device_key,
    f.did_open_app,
    f.did_home,
    f.did_browse,
    f.did_search,
    f.did_view_content,
    f.did_watch_trailer,
    f.did_start_video,
    f.did_complete_video,
    f.did_abandon_video,
    f.did_add_watchlist,
    f.did_rate,
    f.did_subscribe_click,
    f.did_open_app::int               AS n_open_app,
    f.did_browse::int                 AS n_browse,
    f.did_search::int                 AS n_search,
    f.did_view_content::int           AS n_view_content,
    f.did_watch_trailer::int          AS n_watch_trailer,
    f.did_start_video::int            AS n_start_video,
    f.did_complete_video::int         AS n_complete_video,
    f.did_subscribe_click::int        AS n_subscribe_click,
    f.ts_open_app,
    f.ts_first_search,
    f.ts_first_view,
    f.ts_first_start,
    f.ts_first_complete,
    -- Elapsed seconds between adjacent funnel steps. NULL when the later step
    -- never happened, which is a genuinely absent duration rather than zero.
    EXTRACT(epoch FROM (f.ts_first_view  - f.ts_open_app))::int   AS seconds_open_to_view,
    EXTRACT(epoch FROM (f.ts_first_start - f.ts_first_view))::int AS seconds_view_to_start,
    EXTRACT(epoch FROM (f.ts_first_complete - f.ts_first_start))::int
                                                                  AS seconds_start_to_complete,
    f.event_count,
    f.watch_seconds,
    f.max_step_index,
    s.duration_seconds                AS session_duration_seconds,
    s.is_first_session,
    s.entry_screen,
    s.exit_screen
FROM analytics.mv_funnel_steps AS f
JOIN core.sessions AS s USING (session_id);


-- ===========================================================================
-- fact_subscription
--
-- Grain: one row per subscription. Not a daily snapshot: MRR movement needs the
-- start and end of each contract, and expanding to a day grain would multiply
-- 108 rows into tens of thousands for no analytical gain.
-- ===========================================================================
CREATE VIEW powerbi.fact_subscription AS
SELECT
    s.subscription_id                 AS subscription_key,
    s.user_id                         AS user_key,
    s.plan_id                         AS plan_key,
    s.started_on                      AS date_key,
    s.ended_on,
    s.status::text                    AS status,
    s.billing_period::text            AS billing_period,
    s.mrr_usd,
    s.cancel_reason,
    s.is_trial_conversion,
    (s.ended_on IS NULL)              AS is_open,
    -- Contract length in days. NULL while still running, which is the honest
    -- answer — a running subscription has no length yet.
    (s.ended_on - s.started_on)       AS duration_days
FROM core.subscriptions AS s;


-- ===========================================================================
-- fact_experiment_assignment
--
-- Grain: one row per user per experiment. Deliberately carries no verdict: the
-- two-proportion z-test lives in app/services/stats.py, and reimplementing it in
-- DAX would give two answers that drift apart. Use the API's
-- /experiments/{key}/results for significance; use this for exposure counts.
-- ===========================================================================
CREATE VIEW powerbi.fact_experiment_assignment AS
SELECT
    a.experiment_id                   AS experiment_key,
    e.key                             AS experiment_code,
    e.name                            AS experiment_name,
    e.primary_metric,
    e.status::text                    AS experiment_status,
    e.started_on                      AS experiment_started_on,
    e.ended_on                        AS experiment_ended_on,
    a.user_id                         AS user_key,
    a.variant,
    (a.variant = 'control')           AS is_control,
    a.assigned_at,
    a.assigned_at::date               AS date_key
FROM core.experiment_assignments AS a
JOIN core.experiments AS e USING (experiment_id);


COMMIT;

-- ===========================================================================
-- Verification. Run after the script and check the counts look like the
-- database you expect. Rows here should match core/analytics exactly, since
-- every view is a projection with no filtering.
-- ===========================================================================
--   SELECT 'dim_user' AS relation, COUNT(*) FROM powerbi.dim_user
--   UNION ALL SELECT 'dim_content',              COUNT(*) FROM powerbi.dim_content
--   UNION ALL SELECT 'dim_date',                 COUNT(*) FROM powerbi.dim_date
--   UNION ALL SELECT 'fact_user_daily',          COUNT(*) FROM powerbi.fact_user_daily
--   UNION ALL SELECT 'fact_content_daily',       COUNT(*) FROM powerbi.fact_content_daily
--   UNION ALL SELECT 'fact_session_funnel',      COUNT(*) FROM powerbi.fact_session_funnel
--   UNION ALL SELECT 'fact_subscription',        COUNT(*) FROM powerbi.fact_subscription
--   UNION ALL SELECT 'fact_experiment_assignment', COUNT(*) FROM powerbi.fact_experiment_assignment;
