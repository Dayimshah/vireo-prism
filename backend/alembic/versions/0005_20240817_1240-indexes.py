"""Create every index in the core schema.

Revision 0005 of 6. Indexes are separated from table creation on purpose: kept
in one file they can be read as a single artefact, and each one can be justified
against the specific query that needs it. An index nobody can name a query for
is an index that should not exist — it costs write throughput on 3.4M inserts and
buys nothing.

Ordering note
-------------
Indexes are created *before* the seeder runs, not after. That is the slower
choice for bulk loading — every ``COPY`` batch maintains them — and it is
deliberate: it keeps ``alembic upgrade head`` sufficient to reach a fully
queryable schema, so a contributor cannot end up with a seeded database that is
mysteriously slow because a post-load index step was skipped. The seeder issues
``ANALYZE`` afterwards, which is what actually matters for plan quality.

``CONCURRENTLY`` is not used because Alembic wraps migrations in a transaction
and these tables are empty at this point, so there is no lock contention to
avoid.

Revision ID: 0005
Revises: 0004
Created: 2024-08-17 12:40:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all core-schema indexes."""
    # =======================================================================
    # core.users
    #
    # The dimension foreign keys are indexed individually rather than as one
    # composite because the filter bar sends an arbitrary subset — country alone,
    # or channel plus persona — and Postgres can bitmap-AND several single-column
    # indexes. A composite would only serve queries that use its leading column.
    # =======================================================================

    # Cohort queries bucket by signup week/month; this is their driving index.
    op.create_index("ix_users_signup_date", "users", ["signup_date"], schema="core")

    op.create_index("ix_users_country", "users", ["country_id"], schema="core")
    op.create_index("ix_users_channel", "users", ["channel_id"], schema="core")
    op.create_index("ix_users_persona", "users", ["persona_id"], schema="core")
    op.create_index("ix_users_device", "users", ["device_id"], schema="core")

    # Serves `/kpis` premium counts. Partial rather than full: roughly a fifth of
    # users are premium, so indexing only those keeps it small and the planner
    # reaches for it readily.
    op.create_index(
        "ix_users_premium",
        "users",
        ["user_id"],
        schema="core",
        postgresql_where="is_premium",
    )

    # Churn analysis reads the churned population; active users dominate the
    # table and would only dilute the index.
    op.create_index(
        "ix_users_churned_at",
        "users",
        ["churned_at"],
        schema="core",
        postgresql_where="churned_at IS NOT NULL",
    )

    # Recency ranking for the at-risk table and RFM deciles.
    op.create_index(
        "ix_users_last_seen",
        "users",
        ["last_seen_at"],
        schema="core",
        postgresql_where="last_seen_at IS NOT NULL",
    )

    # Composite for the single most common cohort slice: signup cohort split by
    # acquisition channel. Column order matters — signup_date is the range
    # predicate and must lead.
    op.create_index(
        "ix_users_signup_channel",
        "users",
        ["signup_date", "channel_id"],
        schema="core",
    )

    # =======================================================================
    # core.content
    # =======================================================================
    op.create_index("ix_content_genre", "content", ["genre_id"], schema="core")
    op.create_index("ix_content_type", "content", ["content_type"], schema="core")

    # Catalogue leaderboards order by popularity descending.
    op.create_index(
        "ix_content_popularity",
        "content",
        ["popularity_score"],
        schema="core",
        postgresql_using="btree",
        postgresql_ops={"popularity_score": "DESC"},
    )

    # Genre performance matrix groups by both axes.
    op.create_index(
        "ix_content_genre_type",
        "content",
        ["genre_id", "content_type"],
        schema="core",
    )

    # Trigram GIN index backing `/search`. A btree cannot serve
    # `title ILIKE '%shadow%'` because the pattern is unanchored; pg_trgm can.
    op.execute(
        """
        CREATE INDEX ix_content_title_trgm
            ON core.content USING gin (title gin_trgm_ops)
        """
    )

    # "What did we add this quarter" — content shelf-life and freshness analysis.
    op.create_index("ix_content_added_on", "content", ["added_on"], schema="core")

    # =======================================================================
    # core.sessions
    # =======================================================================

    # Per-user session history, newest first. Drives `/users/{id}/journey` and
    # every retention query that walks a user's activity timeline.
    op.create_index(
        "ix_sessions_user_start",
        "sessions",
        ["user_id", "session_start"],
        schema="core",
    )

    # Global time-range scans: DAU, session volume trend, hour-of-day heatmap.
    op.create_index("ix_sessions_start", "sessions", ["session_start"], schema="core")

    op.create_index("ix_sessions_device", "sessions", ["device_id"], schema="core")

    # Duration percentiles sort on this column; the index lets Postgres skip a
    # full sort for windowed percentile queries over a bounded date range.
    op.create_index(
        "ix_sessions_duration",
        "sessions",
        ["duration_seconds"],
        schema="core",
    )

    # First-session lookups feed activation and onboarding funnels. Tiny partial
    # index — one row per user.
    op.create_index(
        "ix_sessions_first",
        "sessions",
        ["user_id", "session_start"],
        schema="core",
        postgresql_where="is_first_session",
    )

    # Sessions that actually contained playback. Excludes browse-only sessions,
    # which are roughly a third of the table, from watch-time aggregations.
    op.create_index(
        "ix_sessions_with_watch",
        "sessions",
        ["session_start", "watch_seconds"],
        schema="core",
        postgresql_where="watch_seconds > 0",
    )

    # =======================================================================
    # core.events — the fact table
    #
    # Indexes created on a partitioned parent cascade to every partition and to
    # any partition created later, so core.ensure_events_partition() needs no
    # index logic of its own.
    # =======================================================================

    # The workhorse. Every per-user time-bounded scan uses it: retention,
    # cohorts, churn features, user journeys.
    op.create_index(
        "ix_events_user_time",
        "events",
        ["user_id", "event_time"],
        schema="core",
    )

    # Reconstructs a session's ordered event trail. step_index rather than
    # event_time so ties at the same timestamp still resolve deterministically —
    # which is exactly what the funnel step logic depends on.
    op.create_index(
        "ix_events_session_step",
        "events",
        ["session_id", "step_index"],
        schema="core",
    )

    # Funnel and event-mix queries filter on event_name first, then time.
    op.create_index(
        "ix_events_name_time",
        "events",
        ["event_name", "event_time"],
        schema="core",
    )

    # Content analytics. Partial because roughly 40% of events are navigation
    # events with a NULL content_id, and excluding them shrinks the index
    # substantially.
    op.create_index(
        "ix_events_content_time",
        "events",
        ["content_id", "event_time"],
        schema="core",
        postgresql_where="content_id IS NOT NULL",
    )

    # Covering index for the completion-rate query, which is the most expensive
    # content query in the catalogue. INCLUDE carries watch_seconds and
    # progress_pct in the leaf pages so the aggregation is index-only and never
    # touches the heap.
    op.execute(
        """
        CREATE INDEX ix_events_playback_covering
            ON core.events (content_id, event_name, event_time)
            INCLUDE (watch_seconds, progress_pct)
            WHERE event_name IN ('START_VIDEO', 'COMPLETE_VIDEO', 'ABANDON_VIDEO')
        """
    )

    # BRIN on event_time. Rows arrive in near-perfect timestamp order, which is
    # the exact access pattern BRIN is built for: a few kilobytes per partition
    # summarising min/max per block range, enough to skip most of a partition on
    # a narrow range scan. Complements rather than replaces the btrees above.
    op.execute(
        """
        CREATE INDEX ix_events_time_brin
            ON core.events USING brin (event_time)
            WITH (pages_per_range = 32)
        """
    )

    # Search-term analysis reads the query string out of the JSONB payload.
    # Expression index so `properties->>'search_query'` is not recomputed per row.
    op.execute(
        """
        CREATE INDEX ix_events_search_query
            ON core.events ((properties ->> 'search_query'))
            WHERE event_name = 'SEARCH'
        """
    )

    # =======================================================================
    # core.subscriptions
    # =======================================================================
    op.create_index(
        "ix_subs_user_started",
        "subscriptions",
        ["user_id", "started_on"],
        schema="core",
    )
    op.create_index("ix_subs_status", "subscriptions", ["status"], schema="core")
    op.create_index("ix_subs_plan", "subscriptions", ["plan_id"], schema="core")
    op.create_index("ix_subs_started_on", "subscriptions", ["started_on"], schema="core")

    # Currently-open subscriptions: the MRR snapshot query, run on every
    # dashboard load.
    op.create_index(
        "ix_subs_active",
        "subscriptions",
        ["user_id", "mrr_usd"],
        schema="core",
        postgresql_where="ended_on IS NULL",
    )

    # Churn-reason mix and cancellation trend.
    op.create_index(
        "ix_subs_ended_on",
        "subscriptions",
        ["ended_on"],
        schema="core",
        postgresql_where="ended_on IS NOT NULL",
    )

    # =======================================================================
    # core.experiment_assignments
    #
    # The composite primary key (experiment_id, user_id) already serves lookups
    # led by experiment_id. These two add the other two access paths.
    # =======================================================================

    # Per-variant metric aggregation — the significance test's driving index.
    op.create_index(
        "ix_assignments_exp_variant",
        "experiment_assignments",
        ["experiment_id", "variant"],
        schema="core",
    )

    # "Which experiments is this user enrolled in", for the user detail page.
    op.create_index(
        "ix_assignments_user",
        "experiment_assignments",
        ["user_id"],
        schema="core",
    )


def downgrade() -> None:
    """Drop every index created by this revision."""
    # Raw SQL with IF EXISTS: the expression, covering and BRIN indexes were
    # created with op.execute and have no op.drop_index counterpart, so treating
    # them uniformly keeps the teardown symmetric.
    for name, table in (
        ("ix_assignments_user", "experiment_assignments"),
        ("ix_assignments_exp_variant", "experiment_assignments"),
        ("ix_subs_ended_on", "subscriptions"),
        ("ix_subs_active", "subscriptions"),
        ("ix_subs_started_on", "subscriptions"),
        ("ix_subs_plan", "subscriptions"),
        ("ix_subs_status", "subscriptions"),
        ("ix_subs_user_started", "subscriptions"),
        ("ix_events_search_query", "events"),
        ("ix_events_time_brin", "events"),
        ("ix_events_playback_covering", "events"),
        ("ix_events_content_time", "events"),
        ("ix_events_name_time", "events"),
        ("ix_events_session_step", "events"),
        ("ix_events_user_time", "events"),
        ("ix_sessions_with_watch", "sessions"),
        ("ix_sessions_first", "sessions"),
        ("ix_sessions_duration", "sessions"),
        ("ix_sessions_device", "sessions"),
        ("ix_sessions_start", "sessions"),
        ("ix_sessions_user_start", "sessions"),
        ("ix_content_added_on", "content"),
        ("ix_content_title_trgm", "content"),
        ("ix_content_genre_type", "content"),
        ("ix_content_popularity", "content"),
        ("ix_content_type", "content"),
        ("ix_content_genre", "content"),
        ("ix_users_signup_channel", "users"),
        ("ix_users_last_seen", "users"),
        ("ix_users_churned_at", "users"),
        ("ix_users_premium", "users"),
        ("ix_users_device", "users"),
        ("ix_users_persona", "users"),
        ("ix_users_channel", "users"),
        ("ix_users_country", "users"),
        ("ix_users_signup_date", "users"),
    ):
        del table  # names are unique within the schema
        op.execute(f"DROP INDEX IF EXISTS core.{name}")
