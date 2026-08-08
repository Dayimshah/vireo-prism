"""Create the four analytics materialized views and the refresh helper.

Revision 0006 of 6 — the last structural change to the database.

Why materialized views and not more tables
------------------------------------------
The frozen architecture allows exactly 13 tables. These four objects are
*derived* — every row is reproducible from ``core`` by aggregation — so making
them tables would create two sources of truth and an obligation to keep them in
sync on write. As materialized views they are disposable: drop them, refresh
them, and the answer is identical.

What they buy is response time. ``mv_user_daily`` collapses 3.4M event rows into
roughly 900k user-day rows, and every retention, cohort and stickiness query
reads from that instead of the fact table. The heaviest query in the catalogue
(the 12-week cohort matrix) goes from seconds to tens of milliseconds.

The narrowness rule
-------------------
None of these views carry ``country_id``, ``channel_id``, ``persona_id`` or
``device_id``, even though almost every dashboard query filters on them. Those
are reachable with one join to ``core.users``, and keeping them out means adding
a new filter dimension never requires rebuilding a view. The single exception is
``signup_date``, denormalised into ``mv_user_daily`` because day-N retention
needs it in *arithmetic* on every row, not as a filter.

Timezone determinism
--------------------
``event_time`` is ``TIMESTAMPTZ``, so a bare ``::date`` cast would resolve
against the connection's ``TimeZone`` setting and produce different day
boundaries for different clients. Every date derivation below pins UTC
explicitly. Same reason the seeder generates in UTC.

Semantics of ``watch_seconds``
------------------------------
On a playback event, ``watch_seconds`` is *incremental* — seconds watched since
that content's previous playback event — which makes ``SUM(watch_seconds)``
correct at any grain. ``progress_pct`` is *cumulative* and monotonic within
``(session_id, content_id)``. This contract is what lets watch time be summed
and completion be measured with a maximum, and ``tests/test_seeder.py`` asserts
that a session's event sum equals its denormalised ``sessions.watch_seconds``.

Revision ID: 0006
Revises: 0005
Created: 2024-08-17 12:50:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Refresh order, and it is load-bearing rather than cosmetic.
#:
#: ``mv_user_lifetime`` reads from ``mv_user_daily`` (it anchors recency and
#: tenure to that view's maximum date), so the daily view must be populated
#: first or lifetime scores are computed against an empty anchor. The other two
#: read only from ``core`` and could go in any position.
#:
#: PostgreSQL will not enforce this for us: a materialized view that selects from
#: another is not automatically refreshed in dependency order.
REFRESH_ORDER: Final[tuple[str, ...]] = (
    "mv_user_daily",
    "mv_content_daily",
    "mv_user_lifetime",
    "mv_funnel_steps",
)


def upgrade() -> None:
    """Create the analytics materialized views, their indexes and the refresh helper."""
    # =======================================================================
    # analytics.mv_user_daily
    #
    # Grain: one row per user per day on which they generated at least one event.
    # Backs: DAU / WAU / MAU, stickiness, all retention curves, all cohort
    #        matrices, new-vs-returning, sessions-per-user.
    #
    # Built from core.events rather than core.sessions because "active" means
    # "did something", and a session that spans midnight legitimately contributes
    # activity to two days. Sessions are counted distinctly within the day.
    #
    # WITH NO DATA: the tables are empty at migration time, so populating now
    # would be pointless work. The seeder issues the first refresh, and
    # analytics.refresh_all() detects the unpopulated state and handles it.
    # =======================================================================
    op.execute(
        """
        CREATE MATERIALIZED VIEW analytics.mv_user_daily AS
        SELECT
            e.user_id,
            (e.event_time AT TIME ZONE 'UTC')::date            AS activity_date,
            u.signup_date,
            ((e.event_time AT TIME ZONE 'UTC')::date - u.signup_date)::int
                                                              AS days_since_signup,
            ((e.event_time AT TIME ZONE 'UTC')::date = u.signup_date)
                                                              AS is_signup_day,

            COUNT(DISTINCT e.session_id)::int                  AS sessions,
            COUNT(*)::int                                      AS events,

            COALESCE(SUM(e.watch_seconds), 0)::bigint          AS watch_seconds,
            COUNT(*) FILTER (WHERE e.event_name = 'START_VIDEO')::int
                                                              AS started_videos,
            COUNT(*) FILTER (WHERE e.event_name = 'COMPLETE_VIDEO')::int
                                                              AS completed_videos,
            COUNT(*) FILTER (WHERE e.event_name = 'ABANDON_VIDEO')::int
                                                              AS abandoned_videos,
            COUNT(*) FILTER (WHERE e.event_name = 'SEARCH')::int
                                                              AS searches,
            COUNT(*) FILTER (WHERE e.event_name = 'SUBSCRIBE_CLICK')::int
                                                              AS subscribe_clicks,
            COUNT(DISTINCT e.content_id) FILTER (WHERE e.content_id IS NOT NULL)::int
                                                              AS distinct_content
        FROM core.events AS e
        JOIN core.users  AS u USING (user_id)
        GROUP BY
            e.user_id,
            (e.event_time AT TIME ZONE 'UTC')::date,
            u.signup_date
        WITH NO DATA
        """
    )

    # REFRESH ... CONCURRENTLY requires a unique index covering every row.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_mv_user_daily
            ON analytics.mv_user_daily (user_id, activity_date)
        """
    )
    # Date-first index for the global time-series scans (DAU trend), which filter
    # on date and aggregate across all users.
    op.execute(
        """
        CREATE INDEX ix_mv_user_daily_date
            ON analytics.mv_user_daily (activity_date)
            INCLUDE (user_id, watch_seconds, sessions)
        """
    )
    # Cohort matrices group by signup_date and bucket days_since_signup.
    op.execute(
        """
        CREATE INDEX ix_mv_user_daily_cohort
            ON analytics.mv_user_daily (signup_date, days_since_signup)
        """
    )

    op.execute(
        "COMMENT ON MATERIALIZED VIEW analytics.mv_user_daily IS "
        "'One row per active user per UTC day. Backs DAU/WAU/MAU, retention and "
        "cohort analysis. Dimensions are reached by joining core.users.'"
    )

    # =======================================================================
    # analytics.mv_content_daily
    #
    # Grain: one row per title per day it was engaged with.
    # Backs: content leaderboard, completion rate, trailer-to-start conversion,
    #        genre performance, shelf-life decay.
    #
    # Completion rate is deliberately *not* pre-computed as a ratio here. Storing
    # numerator and denominator separately is what makes the metric correctly
    # aggregatable: SUM(completions) / SUM(starts) over a date range is right,
    # whereas averaging a stored per-day ratio is Simpson's paradox waiting to
    # happen.
    # =======================================================================
    op.execute(
        """
        CREATE MATERIALIZED VIEW analytics.mv_content_daily AS
        SELECT
            e.content_id,
            (e.event_time AT TIME ZONE 'UTC')::date            AS activity_date,

            COUNT(DISTINCT e.user_id)::int                     AS unique_viewers,
            COUNT(*) FILTER (WHERE e.event_name = 'VIEW_CONTENT')::int
                                                              AS detail_views,
            COUNT(*) FILTER (WHERE e.event_name = 'WATCH_TRAILER')::int
                                                              AS trailer_views,
            COUNT(*) FILTER (WHERE e.event_name = 'START_VIDEO')::int
                                                              AS starts,
            COUNT(*) FILTER (WHERE e.event_name = 'COMPLETE_VIDEO')::int
                                                              AS completions,
            COUNT(*) FILTER (WHERE e.event_name = 'ABANDON_VIDEO')::int
                                                              AS abandons,
            COUNT(*) FILTER (WHERE e.event_name = 'ADD_TO_WATCHLIST')::int
                                                              AS watchlist_adds,
            COUNT(*) FILTER (WHERE e.event_name = 'RATE')::int AS ratings,

            COALESCE(SUM(e.watch_seconds), 0)::bigint          AS watch_seconds,

            -- Mean rating for the day. NULL, not zero, when nobody rated:
            -- an unrated title must not drag a genre average toward zero.
            AVG((e.properties ->> 'rating')::numeric)
                FILTER (WHERE e.event_name = 'RATE')           AS avg_rating,

            -- How far the average abandoner got. The single most actionable
            -- content metric: a title abandoned at 15% has a different problem
            -- from one abandoned at 80%.
            AVG(e.progress_pct) FILTER (WHERE e.event_name = 'ABANDON_VIDEO')
                                                              AS avg_abandon_pct
        FROM core.events AS e
        WHERE e.content_id IS NOT NULL
        GROUP BY
            e.content_id,
            (e.event_time AT TIME ZONE 'UTC')::date
        WITH NO DATA
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_mv_content_daily
            ON analytics.mv_content_daily (content_id, activity_date)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mv_content_daily_date
            ON analytics.mv_content_daily (activity_date)
            INCLUDE (content_id, watch_seconds, starts, completions)
        """
    )

    op.execute(
        "COMMENT ON MATERIALIZED VIEW analytics.mv_content_daily IS "
        "'One row per title per UTC day. Numerators and denominators are stored "
        "separately so ratios aggregate correctly over any date range.'"
    )

    # =======================================================================
    # analytics.mv_user_lifetime
    #
    # Grain: one row per user who has ever been active.
    # Backs: LTV, power-user RFM deciles, the churn scorecard, /users/{id}.
    #
    # This is the widest of the four views and the only one that reaches outside
    # the event stream, because lifetime value needs subscription revenue. The
    # two aggregates are computed in separate CTEs and joined, rather than in one
    # pass: a user with three subscriptions and four hundred events would
    # otherwise fan out to twelve hundred rows before grouping, and both sums
    # would be wrong.
    # =======================================================================
    op.execute(
        """
        CREATE MATERIALIZED VIEW analytics.mv_user_lifetime AS
        WITH activity AS (
            SELECT
                d.user_id,
                MIN(d.activity_date)                          AS first_active_date,
                MAX(d.activity_date)                          AS last_active_date,
                COUNT(*)::int                                 AS active_days,
                SUM(d.sessions)::int                          AS total_sessions,
                SUM(d.events)::bigint                         AS total_events,
                SUM(d.watch_seconds)::bigint                  AS total_watch_seconds,
                SUM(d.started_videos)::int                    AS started_videos,
                SUM(d.completed_videos)::int                  AS completed_videos,
                SUM(d.abandoned_videos)::int                  AS abandoned_videos,
                SUM(d.searches)::int                          AS searches,
                SUM(d.subscribe_clicks)::int                  AS subscribe_clicks,

                -- Engagement in the trailing 28 days, evaluated against the
                -- dataset's own maximum date rather than CURRENT_DATE so the
                -- churn scorecard stays stable no matter when it is queried.
                SUM(d.watch_seconds) FILTER (
                    WHERE d.activity_date > (SELECT MAX(activity_date)
                                             FROM analytics.mv_user_daily) - 28
                )::bigint                                     AS watch_seconds_28d,
                COUNT(*) FILTER (
                    WHERE d.activity_date > (SELECT MAX(activity_date)
                                             FROM analytics.mv_user_daily) - 28
                )::int                                        AS active_days_28d
            FROM analytics.mv_user_daily AS d
            GROUP BY d.user_id
        ),
        breadth AS (
            SELECT
                e.user_id,
                COUNT(DISTINCT e.content_id)::int             AS distinct_content,
                COUNT(DISTINCT c.genre_id)::int               AS distinct_genres,
                COUNT(DISTINCT s.device_id)::int              AS distinct_devices
            FROM core.events   AS e
            JOIN core.sessions AS s USING (session_id)
            LEFT JOIN core.content AS c ON c.content_id = e.content_id
            GROUP BY e.user_id
        ),
        revenue AS (
            SELECT
                sub.user_id,
                COUNT(*)::int                                 AS subscription_count,
                MIN(sub.started_on)                           AS first_subscribed_on,
                MAX(sub.ended_on)                             AS last_ended_on,
                BOOL_OR(sub.ended_on IS NULL)                 AS has_active_subscription,
                BOOL_OR(sub.is_trial_conversion)              AS ever_converted_trial,
                COALESCE(SUM(sub.mrr_usd) FILTER (WHERE sub.ended_on IS NULL), 0)
                                                              AS current_mrr_usd,

                -- Realised revenue: MRR multiplied by whole months served, with
                -- an open subscription measured to the dataset's last event date.
                -- GREATEST(...,1) bills a partial first month, which is how
                -- subscription billing actually works.
                COALESCE(SUM(
                    sub.mrr_usd * GREATEST(
                        1,
                        (EXTRACT(YEAR  FROM age(
                             COALESCE(sub.ended_on,
                                      (SELECT MAX(activity_date) FROM analytics.mv_user_daily)),
                             sub.started_on)) * 12
                         + EXTRACT(MONTH FROM age(
                             COALESCE(sub.ended_on,
                                      (SELECT MAX(activity_date) FROM analytics.mv_user_daily)),
                             sub.started_on)))::int
                    )
                ), 0)                                         AS lifetime_revenue_usd
            FROM core.subscriptions AS sub
            GROUP BY sub.user_id
        )
        SELECT
            u.user_id,
            u.signup_date,
            u.churned_at,
            u.is_premium,

            a.first_active_date,
            a.last_active_date,
            a.active_days,
            a.total_sessions,
            a.total_events,
            a.total_watch_seconds,
            a.started_videos,
            a.completed_videos,
            a.abandoned_videos,
            a.searches,
            a.subscribe_clicks,
            COALESCE(a.watch_seconds_28d, 0)                  AS watch_seconds_28d,
            COALESCE(a.active_days_28d, 0)                    AS active_days_28d,

            COALESCE(b.distinct_content, 0)                   AS distinct_content,
            COALESCE(b.distinct_genres, 0)                    AS distinct_genres,
            COALESCE(b.distinct_devices, 0)                   AS distinct_devices,

            COALESCE(r.subscription_count, 0)                 AS subscription_count,
            r.first_subscribed_on,
            COALESCE(r.has_active_subscription, false)        AS has_active_subscription,
            COALESCE(r.ever_converted_trial, false)           AS ever_converted_trial,
            COALESCE(r.current_mrr_usd, 0)                    AS current_mrr_usd,
            COALESCE(r.lifetime_revenue_usd, 0)               AS lifetime_revenue_usd,

            -- Derived engagement ratios. NULLIF guards the zero denominators;
            -- a user who never started a video has no completion rate, which is
            -- different from a completion rate of zero.
            (a.total_watch_seconds::numeric / NULLIF(a.total_sessions, 0))
                                                              AS avg_watch_seconds_per_session,
            (a.completed_videos::numeric / NULLIF(a.started_videos, 0))
                                                              AS completion_rate,
            (a.total_sessions::numeric / NULLIF(a.active_days, 0))
                                                              AS sessions_per_active_day,

            -- Tenure and recency, both anchored to the dataset's last event date
            -- so these columns do not drift as real-world time passes.
            ((SELECT MAX(activity_date) FROM analytics.mv_user_daily) - u.signup_date)::int
                                                              AS tenure_days,
            ((SELECT MAX(activity_date) FROM analytics.mv_user_daily) - a.last_active_date)::int
                                                              AS days_since_last_active
        FROM core.users AS u
        JOIN activity   AS a USING (user_id)
        LEFT JOIN breadth AS b USING (user_id)
        LEFT JOIN revenue AS r USING (user_id)
        WITH NO DATA
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_mv_user_lifetime
            ON analytics.mv_user_lifetime (user_id)
        """
    )
    # Recency ranking for the at-risk table and RFM decile assignment.
    op.execute(
        """
        CREATE INDEX ix_mv_user_lifetime_recency
            ON analytics.mv_user_lifetime (days_since_last_active)
            INCLUDE (total_watch_seconds, completion_rate)
        """
    )
    # LTV leaderboards and the power-user decile query order by revenue.
    op.execute(
        """
        CREATE INDEX ix_mv_user_lifetime_revenue
            ON analytics.mv_user_lifetime (lifetime_revenue_usd DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mv_user_lifetime_cohort
            ON analytics.mv_user_lifetime (signup_date)
        """
    )

    op.execute(
        "COMMENT ON MATERIALIZED VIEW analytics.mv_user_lifetime IS "
        "'One row per ever-active user. Recency and tenure are anchored to the "
        "dataset maximum date, not CURRENT_DATE, so scores are reproducible.'"
    )

    # =======================================================================
    # analytics.mv_funnel_steps
    #
    # Grain: one row per session, with a boolean per funnel step.
    # Backs: every funnel, drop-off analysis, time-between-steps.
    #
    # The reshape is the point. A funnel over the raw event table needs a
    # self-join or a window function per step; against this view each step is one
    # COUNT(*) FILTER over a boolean, so an eight-step funnel is a single
    # sequential scan of ~420k narrow rows.
    #
    # Timestamps of first occurrence are carried alongside the flags so
    # funnel_time_between_steps needs no second pass over core.events.
    # =======================================================================
    op.execute(
        """
        CREATE MATERIALIZED VIEW analytics.mv_funnel_steps AS
        SELECT
            e.session_id,
            e.user_id,
            (MIN(e.event_time) AT TIME ZONE 'UTC')::date       AS session_date,

            BOOL_OR(e.event_name = 'OPEN_APP')                 AS did_open_app,
            BOOL_OR(e.event_name = 'HOME')                     AS did_home,
            BOOL_OR(e.event_name = 'BROWSE_GENRE')             AS did_browse,
            BOOL_OR(e.event_name = 'SEARCH')                   AS did_search,
            BOOL_OR(e.event_name = 'VIEW_CONTENT')             AS did_view_content,
            BOOL_OR(e.event_name = 'WATCH_TRAILER')            AS did_watch_trailer,
            BOOL_OR(e.event_name = 'START_VIDEO')              AS did_start_video,
            BOOL_OR(e.event_name = 'COMPLETE_VIDEO')           AS did_complete_video,
            BOOL_OR(e.event_name = 'ABANDON_VIDEO')            AS did_abandon_video,
            BOOL_OR(e.event_name = 'ADD_TO_WATCHLIST')         AS did_add_watchlist,
            BOOL_OR(e.event_name = 'RATE')                     AS did_rate,
            BOOL_OR(e.event_name = 'SUBSCRIBE_CLICK')          AS did_subscribe_click,

            MIN(e.event_time) FILTER (WHERE e.event_name = 'OPEN_APP')
                                                              AS ts_open_app,
            MIN(e.event_time) FILTER (WHERE e.event_name = 'SEARCH')
                                                              AS ts_first_search,
            MIN(e.event_time) FILTER (WHERE e.event_name = 'VIEW_CONTENT')
                                                              AS ts_first_view,
            MIN(e.event_time) FILTER (WHERE e.event_name = 'START_VIDEO')
                                                              AS ts_first_start,
            MIN(e.event_time) FILTER (WHERE e.event_name = 'COMPLETE_VIDEO')
                                                              AS ts_first_complete,

            COUNT(*)::int                                      AS event_count,
            COALESCE(SUM(e.watch_seconds), 0)::int             AS watch_seconds,
            MAX(e.step_index)::int                             AS max_step_index
        FROM core.events AS e
        GROUP BY e.session_id, e.user_id
        WITH NO DATA
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_mv_funnel_steps
            ON analytics.mv_funnel_steps (session_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mv_funnel_steps_date
            ON analytics.mv_funnel_steps (session_date)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mv_funnel_steps_user
            ON analytics.mv_funnel_steps (user_id)
        """
    )

    op.execute(
        "COMMENT ON MATERIALIZED VIEW analytics.mv_funnel_steps IS "
        "'One row per session with a boolean per funnel step, so an N-step funnel "
        "is N filtered counts over one scan instead of N self-joins.'"
    )

    # =======================================================================
    # analytics.refresh_all
    #
    # Replaces the no-op stub from revision 0001 now that the views exist.
    #
    # The relispopulated check is the operationally important part: PostgreSQL
    # rejects REFRESH ... CONCURRENTLY on a view that has never held data, and
    # every view above was created WITH NO DATA. Without this branch the first
    # `make refresh` on a fresh database would fail with a message that reads
    # like a bug. Instead the function silently does the initial populate
    # non-concurrently, then uses CONCURRENTLY from then on.
    #
    # CONCURRENTLY takes no exclusive lock, so the dashboard keeps serving the
    # previous snapshot while a refresh runs. It costs roughly twice the time and
    # requires the unique indexes created above.
    # =======================================================================
    view_list = ", ".join(f"'{name}'" for name in REFRESH_ORDER)
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION analytics.refresh_all(concurrent boolean DEFAULT true)
        RETURNS TABLE (view_name text, duration_ms numeric)
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_view        text;
            v_started     timestamptz;
            v_populated   boolean;
            v_concurrent  boolean;
        BEGIN
            FOREACH v_view IN ARRAY ARRAY[{view_list}]::text[]
            LOOP
                SELECT c.relispopulated
                  INTO v_populated
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'analytics' AND c.relname = v_view;

                IF v_populated IS NULL THEN
                    RAISE EXCEPTION 'materialized view analytics.% does not exist', v_view;
                END IF;

                -- CONCURRENTLY is impossible on a never-populated view.
                v_concurrent := concurrent AND v_populated;

                v_started := clock_timestamp();

                IF v_concurrent THEN
                    EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.%I', v_view);
                ELSE
                    EXECUTE format('REFRESH MATERIALIZED VIEW analytics.%I', v_view);
                END IF;

                -- Fresh statistics immediately after, so the planner does not
                -- spend the next hour costing these views as if they were empty.
                EXECUTE format('ANALYZE analytics.%I', v_view);

                RETURN QUERY SELECT
                    v_view,
                    ROUND(EXTRACT(EPOCH FROM (clock_timestamp() - v_started)) * 1000, 1);
            END LOOP;
        END;
        $$
        """
    )
    op.execute(
        "COMMENT ON FUNCTION analytics.refresh_all(boolean) IS "
        "'Refresh every analytics materialized view and ANALYZE it. Falls back to "
        "a non-concurrent refresh for views that have never been populated. "
        "Returns per-view timings.'"
    )


def downgrade() -> None:
    """Drop the materialized views and restore the no-op refresh stub."""
    for name in reversed(REFRESH_ORDER):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS analytics.{name} CASCADE")

    # Restore revision 0001's stub rather than dropping the function, so the
    # downgrade lands on exactly the state revision 0005 expects.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION analytics.refresh_all(concurrent boolean DEFAULT true)
        RETURNS TABLE (view_name text, duration_ms numeric)
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RETURN;
        END;
        $$
        """
    )
