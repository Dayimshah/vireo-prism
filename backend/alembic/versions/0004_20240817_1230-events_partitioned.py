"""Create core.events as a monthly range-partitioned table.

Revision 0004 of 6. This is the fact table — ~3.4M rows at the medium seed
profile, ~14M at large — and the only object in the schema that needs a
partitioning strategy.

Why partition at all
--------------------
Every analytical query in this project is time-bounded: the API rejects an
unbounded window, and the dashboard always sends a date range. Range
partitioning on ``event_time`` turns that filter into *partition pruning*, so a
"last 30 days" question touches one or two partitions instead of eighteen.
Combined with ``enable_partitionwise_aggregate`` (set in the Postgres container
config), the DAU and funnel queries aggregate each partition independently and
in parallel.

Two secondary wins that matter more than they sound:

* ``VACUUM`` and ``REFRESH MATERIALIZED VIEW`` work on a bounded slice rather
  than the whole fact table.
* Dropping old data becomes ``DROP TABLE core.events_2024_01`` — instant, and no
  bloat — instead of a ``DELETE`` that leaves dead tuples behind.

Why monthly, not daily
----------------------
Daily partitions over 18 months would mean 550 partitions. Planning time grows
with partition count, and at this data volume the planner overhead would exceed
the pruning benefit. Monthly gives 18 partitions of roughly 190k rows each —
small enough to scan quickly, few enough to plan over cheaply.

The composite primary key
-------------------------
PostgreSQL requires the partition key to be part of every unique constraint on a
partitioned table, so the primary key is ``(event_id, event_time)`` rather than
``event_id`` alone. ``event_id`` remains globally unique in practice because it
draws from one shared sequence; the compound key is a structural requirement, not
a modelling decision.

Revision ID: 0004
Revises: 0003
Created: 2024-08-17 12:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: How far back partitions are pre-created, relative to the current month.
#:
#: The simulation window is 18 months, but ``PRISM_SEED__WINDOW_MONTHS`` is
#: configurable up to 60 and a contributor may seed with an older
#: ``PRISM_SEED__WINDOW_END``. Pre-creating 60 months of empty partitions costs
#: a few hundred kilobytes of catalogue and removes an entire class of
#: "rows landed in events_default" support question.
MONTHS_BACK: Final[int] = 60

#: Months created ahead of the current month. Small, but non-zero: a session
#: that starts at 23:50 on the last day of a month can legitimately emit events
#: after midnight, and generating against a UTC boundary from a positive-offset
#: timezone can land a few rows in the following month.
MONTHS_FORWARD: Final[int] = 3

#: Screen names an event may occur on. Mirrors the session-level list in
#: revision 0003 with the addition of ``player``-adjacent surfaces.
SCREENS: Final[tuple[str, ...]] = (
    "splash",
    "home",
    "search",
    "browse",
    "detail",
    "player",
    "watchlist",
    "profile",
    "settings",
    "paywall",
)

_SCREEN_LIST: Final[str] = ", ".join(f"'{screen}'" for screen in SCREENS)


def upgrade() -> None:
    """Create the partitioned events table, its partition helper, and partitions."""
    # =======================================================================
    # The parent table
    #
    # Written as raw SQL rather than op.create_table because the partitioning
    # clause, the composite primary key over a serial column, and the ordering of
    # the PARTITION BY clause are all easier to read and verify in plain DDL than
    # through the SQLAlchemy dialect kwargs that would produce the same thing.
    # =======================================================================
    op.execute(
        f"""
        CREATE TABLE core.events (
            event_id       BIGSERIAL     NOT NULL,
            session_id     BIGINT        NOT NULL,
            user_id        BIGINT        NOT NULL,
            content_id     BIGINT        NULL,
            event_time     TIMESTAMPTZ   NOT NULL,
            event_name     core.event_name NOT NULL,
            screen         VARCHAR(32)   NOT NULL,
            step_index     SMALLINT      NOT NULL,
            watch_seconds  INTEGER       NULL,
            progress_pct   NUMERIC(5, 2) NULL,
            properties     JSONB         NOT NULL DEFAULT '{{}}'::jsonb,

            CONSTRAINT pk_events PRIMARY KEY (event_id, event_time),

            CONSTRAINT fk_events_session FOREIGN KEY (session_id)
                REFERENCES core.sessions (session_id) ON DELETE CASCADE,
            CONSTRAINT fk_events_user FOREIGN KEY (user_id)
                REFERENCES core.users (user_id) ON DELETE CASCADE,
            CONSTRAINT fk_events_content FOREIGN KEY (content_id)
                REFERENCES core.content (content_id) ON DELETE RESTRICT,

            -- The stated requirement, enforced rather than trusted.
            CONSTRAINT ck_events_no_future_time CHECK (event_time <= now()),

            CONSTRAINT ck_events_step_index CHECK (step_index >= 0),
            CONSTRAINT ck_events_screen CHECK (screen IN ({_SCREEN_LIST})),

            -- Playback columns are populated only by playback events. Enforcing
            -- the pairing means the watch-time SQL can SUM(watch_seconds)
            -- without a defensive CASE, and a generator bug that attaches
            -- progress to a SEARCH event fails loudly at insert time.
            CONSTRAINT ck_events_watch_only_on_playback CHECK (
                watch_seconds IS NULL
                OR event_name IN (
                    'VIDEO_PROGRESS', 'PAUSE_VIDEO', 'ABANDON_VIDEO', 'COMPLETE_VIDEO'
                )
            ),
            CONSTRAINT ck_events_progress_only_on_playback CHECK (
                progress_pct IS NULL
                OR event_name IN (
                    'VIDEO_PROGRESS', 'PAUSE_VIDEO', 'ABANDON_VIDEO', 'COMPLETE_VIDEO'
                )
            ),
            CONSTRAINT ck_events_watch_non_negative CHECK (
                watch_seconds IS NULL OR watch_seconds >= 0
            ),
            CONSTRAINT ck_events_progress_range CHECK (
                progress_pct IS NULL OR progress_pct BETWEEN 0 AND 100
            ),

            -- A completion is 90%+ watched by definition. This is the constraint
            -- that makes content_completion_rate trustworthy: the metric cannot
            -- be inflated by a COMPLETE_VIDEO event that never really finished.
            CONSTRAINT ck_events_complete_is_complete CHECK (
                event_name <> 'COMPLETE_VIDEO'
                OR (progress_pct IS NOT NULL AND progress_pct >= 90)
            ),

            -- Content-scoped events must name their content; navigation events
            -- must not. Prevents the ambiguous middle ground where a VIEW_CONTENT
            -- row has a NULL content_id and silently drops out of every join.
            CONSTRAINT ck_events_content_id_presence CHECK (
                CASE
                    WHEN event_name IN (
                        'VIEW_CONTENT', 'WATCH_TRAILER', 'START_VIDEO', 'VIDEO_PROGRESS',
                        'PAUSE_VIDEO', 'ABANDON_VIDEO', 'COMPLETE_VIDEO',
                        'ADD_TO_WATCHLIST', 'RATE'
                    ) THEN content_id IS NOT NULL
                    WHEN event_name IN ('OPEN_APP', 'HOME', 'SEARCH', 'EXIT')
                        THEN content_id IS NULL
                    ELSE true          -- BROWSE_GENRE and SUBSCRIBE_CLICK: either is valid
                END
            ),

            CONSTRAINT ck_events_properties_is_object CHECK (
                jsonb_typeof(properties) = 'object'
            )
        ) PARTITION BY RANGE (event_time)
        """
    )

    op.execute(
        "COMMENT ON TABLE core.events IS "
        "'Clickstream fact table, range-partitioned monthly on event_time. "
        "Journey invariants (START before COMPLETE, RATE after COMPLETE, monotonic "
        "progress) are cross-row and enforced by the generator plus tests/test_seeder.py, "
        "not by CHECK constraints.'"
    )
    op.execute(
        "COMMENT ON COLUMN core.events.step_index IS "
        "'Zero-based position of this event within its session. Makes funnel "
        "ordering deterministic when two events share a timestamp.'"
    )
    op.execute(
        "COMMENT ON COLUMN core.events.properties IS "
        "'Event-specific payload: search_query, rating, genre_browsed, error_code. "
        "JSONB keeps the taxonomy extensible without a schema change.'"
    )

    # =======================================================================
    # Partition helper
    #
    # Idempotent and callable at any time, which matters for three reasons:
    # this migration uses it to backfill, the seeder calls it before loading in
    # case a custom WINDOW_END falls outside the pre-created range, and an
    # operator can extend the table by hand without writing DDL.
    # =======================================================================
    op.execute(
        """
        CREATE OR REPLACE FUNCTION core.ensure_events_partition(p_month date)
        RETURNS text
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_start date := date_trunc('month', p_month)::date;
            v_end   date := (date_trunc('month', p_month) + interval '1 month')::date;
            v_name  text := format('events_%s', to_char(v_start, 'YYYY_MM'));
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'core' AND c.relname = v_name
            ) THEN
                RETURN v_name;   -- already present; nothing to do
            END IF;

            EXECUTE format(
                'CREATE TABLE core.%I PARTITION OF core.events '
                'FOR VALUES FROM (%L) TO (%L)',
                v_name, v_start, v_end
            );

            RETURN v_name;
        END;
        $$
        """
    )
    op.execute(
        "COMMENT ON FUNCTION core.ensure_events_partition(date) IS "
        "'Idempotently create the monthly partition containing p_month. "
        "Returns the partition name.'"
    )

    # =======================================================================
    # Partitions
    #
    # A DEFAULT partition is created first and deliberately kept. It is a safety
    # net: a row outside every declared range lands there instead of raising, so
    # a long seeding run cannot fail on its last batch over a boundary
    # miscalculation. tests/test_seeder.py asserts it stays empty, which is how
    # the net gets noticed if it is ever used.
    # =======================================================================
    op.execute("CREATE TABLE core.events_default PARTITION OF core.events DEFAULT")
    op.execute(
        "COMMENT ON TABLE core.events_default IS "
        "'Catch-all for rows outside every declared month. Expected to stay empty; "
        "non-zero rows here mean the partition range needs extending.'"
    )

    op.execute(
        f"""
        DO $$
        DECLARE
            v_month date;
        BEGIN
            FOR v_month IN
                SELECT generate_series(
                    date_trunc('month', CURRENT_DATE - interval '{MONTHS_BACK} months'),
                    date_trunc('month', CURRENT_DATE + interval '{MONTHS_FORWARD} months'),
                    interval '1 month'
                )::date
            LOOP
                PERFORM core.ensure_events_partition(v_month);
            END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    """Drop the events table, every partition, and the partition helper."""
    # CASCADE removes all attached partitions in one statement; dropping them
    # individually would require re-deriving the same month list.
    op.execute("DROP TABLE IF EXISTS core.events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS core.ensure_events_partition(date)")
