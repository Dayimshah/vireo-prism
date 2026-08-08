"""Create the core and analytics schemas plus every shared enum type.

Revision 0001 of 6. Establishes the namespaces and domain vocabularies that all
later revisions build on. Nothing here holds data.

Two schemas, with a hard boundary between them:

``core``
    The 13 normalized tables. Written by the seeder, read by everything.

``analytics``
    Materialized views only, created in revision 0006. Nothing writes here
    directly; it is a derived presentation layer for the API's hot paths and for
    Power BI's DirectQuery star schema.

Enums rather than lookup tables
-------------------------------
Five vocabularies are modelled as PostgreSQL enum types instead of dimension
tables: content type, event name, subscription status, billing period and
experiment status. The distinction applied throughout this schema is whether a
value set is *editable by the business* or *structural to the code*.

Countries, devices, channels, personas, genres and plans are business data —
rows get added without a deploy, and they carry attributes (CAC, price, region).
They are tables.

An event name, by contrast, cannot be added without the generator and the
funnel SQL both changing. Encoding it as an enum makes the database reject a
typo at write time, costs 4 bytes instead of a join, and documents the taxonomy
in one authoritative place.

Revision ID: 0001
Revises: None
Created: 2024-08-17 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Enum definitions
#
# Ordering is significant and permanent: PostgreSQL derives an enum's sort order
# from declaration order, and `ORDER BY event_name` in the funnel queries relies
# on it. Appending a value later is cheap (ALTER TYPE ... ADD VALUE); reordering
# is not. These are frozen.
# ---------------------------------------------------------------------------

#: What kind of thing a catalogue entry is.
CONTENT_TYPE: Final[tuple[str, ...]] = (
    "movie",
    "series",
    "documentary",
    "stand_up",
)

#: The clickstream taxonomy — 15 values, declared in the order a user would
#: naturally encounter them so that enum sort order matches funnel order.
#:
#: Invariants enforced by the generator and asserted in tests:
#:   * every session opens with OPEN_APP and closes with EXIT
#:   * START_VIDEO requires a prior VIEW_CONTENT on the same content_id
#:   * COMPLETE_VIDEO and ABANDON_VIDEO are mutually exclusive per (session, content)
#:   * RATE only follows COMPLETE_VIDEO
EVENT_NAME: Final[tuple[str, ...]] = (
    "OPEN_APP",
    "HOME",
    "BROWSE_GENRE",
    "SEARCH",
    "VIEW_CONTENT",
    "WATCH_TRAILER",
    "START_VIDEO",
    "VIDEO_PROGRESS",
    "PAUSE_VIDEO",
    "ABANDON_VIDEO",
    "COMPLETE_VIDEO",
    "ADD_TO_WATCHLIST",
    "RATE",
    "SUBSCRIBE_CLICK",
    "EXIT",
)

#: Subscription lifecycle. ``trialing`` precedes ``active`` for a converting
#: user; ``cancelled`` means the user opted out, ``expired`` means payment
#: lapsed. The distinction drives the churn-reason mix.
SUB_STATUS: Final[tuple[str, ...]] = (
    "trialing",
    "active",
    "paused",
    "cancelled",
    "expired",
)

#: Billing cadence. Drives the MRR normalisation in the revenue queries: an
#: annual plan's monthly recurring revenue is its price divided by twelve.
BILLING_PERIOD: Final[tuple[str, ...]] = (
    "monthly",
    "quarterly",
    "annual",
)

#: Experiment lifecycle. ``stopped`` distinguishes an experiment killed early
#: from one that ran to its planned end, which matters when reading results.
EXP_STATUS: Final[tuple[str, ...]] = (
    "running",
    "completed",
    "stopped",
)

#: Every enum this revision owns, in creation order.
ENUMS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("content_type", CONTENT_TYPE),
    ("event_name", EVENT_NAME),
    ("sub_status", SUB_STATUS),
    ("billing_period", BILLING_PERIOD),
    ("exp_status", EXP_STATUS),
)


def _quote(values: tuple[str, ...]) -> str:
    """Render enum labels as a SQL value list.

    Args:
        values: Enum labels. All are module-level constants, never user input.

    Returns:
        A comma-separated, single-quoted list for a ``CREATE TYPE`` statement.
    """
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    """Create schemas, enum types and the shared MV-refresh helper."""
    # -----------------------------------------------------------------------
    # Schemas
    # -----------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")

    op.execute("COMMENT ON SCHEMA core IS 'Normalized source-of-truth tables for Vireo product data.'")
    op.execute(
        "COMMENT ON SCHEMA analytics IS "
        "'Derived materialized views. Rebuilt from core; never written to directly.'"
    )

    # -----------------------------------------------------------------------
    # Extensions
    #
    # pg_stat_statements is preloaded by the container's shared_preload_libraries
    # but still needs its extension row created before the view exists. It backs
    # the query-performance notes in docs/decisions.md.
    # -----------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")  # trigram index for /search

    # -----------------------------------------------------------------------
    # Enum types
    # -----------------------------------------------------------------------
    for name, values in ENUMS:
        op.execute(f"CREATE TYPE core.{name} AS ENUM ({_quote(values)})")

    op.execute(
        "COMMENT ON TYPE core.event_name IS "
        "'Clickstream taxonomy. Declaration order matches natural funnel order.'"
    )

    # -----------------------------------------------------------------------
    # Refresh helper
    #
    # Declared here rather than in 0006 so that the function's signature is
    # stable from the very first migration and `make refresh` works even
    # against a partially migrated database. The body is replaced in 0006 once
    # the views it names actually exist.
    #
    # SECURITY INVOKER (the default) is correct: refreshing must require the
    # caller's own privileges, not the definer's.
    # -----------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION analytics.refresh_all(concurrent boolean DEFAULT true)
        RETURNS TABLE (view_name text, duration_ms numeric)
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- No-op until revision 0006 defines the views and replaces this body.
            RETURN;
        END;
        $$
        """
    )
    op.execute(
        "COMMENT ON FUNCTION analytics.refresh_all(boolean) IS "
        "'Refresh every analytics materialized view in dependency order. "
        "Pass concurrent => false for the initial populate.'"
    )


def downgrade() -> None:
    """Drop the enum types, helper function and both schemas."""
    op.execute("DROP FUNCTION IF EXISTS analytics.refresh_all(boolean)")

    for name, _ in reversed(ENUMS):
        op.execute(f"DROP TYPE IF EXISTS core.{name}")

    # CASCADE is intentional: by this point later revisions have already dropped
    # their own objects, so anything left is residue from a failed partial run.
    op.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
    op.execute("DROP SCHEMA IF EXISTS core CASCADE")
