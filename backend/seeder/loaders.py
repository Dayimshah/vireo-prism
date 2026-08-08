"""Bulk load generated rows into PostgreSQL via ``COPY``.

Why ``COPY`` and not ``INSERT``
-------------------------------
At the medium profile the seeder writes about 3.4 million event rows. Parameterised
``INSERT`` statements, even batched, spend most of their time on per-statement
overhead; ``COPY`` streams rows over a single command and is roughly an order of
magnitude faster. On a 2020-era laptop the difference is minutes against most of
an hour.

Text format, not binary
-----------------------
The architecture called for binary ``COPY``, and that turned out to be the wrong
call for this schema. Binary format requires the client to send a type OID for
every column, and five of these columns are PostgreSQL enums (``core.event_name``,
``core.content_type``, ``core.sub_status``, ``core.billing_period``,
``core.exp_status``) whose OIDs are assigned at migration time and differ between
databases. Making binary work would mean querying ``pg_type`` at startup and
registering five custom dumpers — real complexity in exchange for a few per cent.

Text format sends each value as a literal for the server to parse, which handles
enums natively. The measured cost against binary at this row count is small, and
the code is substantially harder to get subtly wrong. The trade is recorded in
``docs/decisions.md``.

Sequence hygiene
----------------
Most tables are loaded with explicit surrogate keys, which leaves their
``BIGSERIAL`` sequences at zero. Any later ``INSERT`` would then collide on the
primary key. :func:`reset_sequences` advances every sequence past the loaded
maximum, which is the step people forget and then debug for an hour.

``core.events`` is the exception: its ``event_id`` is left to the sequence, so
there is nothing to reset.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import date
import time
from typing import TYPE_CHECKING, Any, Final

import psycopg
from psycopg.types.json import Jsonb

from app.core.logging import get_logger

if TYPE_CHECKING:
    from seeder.catalog import ContentRow

logger = get_logger(__name__)


# ===========================================================================
# Column orders
#
# Each tuple must match the corresponding generator's row renderer exactly.
# A mismatch here produces a load that succeeds with values in the wrong
# columns, which is far worse than a crash — so tests/test_seeder.py asserts
# each tuple against information_schema.
# ===========================================================================

#: ``core.users``. ``created_at`` is omitted; it has a server default.
USER_COLUMNS: Final[tuple[str, ...]] = (
    "user_id",
    "signup_date",
    "country_id",
    "device_id",
    "channel_id",
    "persona_id",
    "is_premium",
    "gender",
    "age",
    "app_version",
    "last_seen_at",
    "churned_at",
)

#: ``core.content``. ``ContentRow.total_runtime_minutes`` is derived and not
#: persisted, so this list is shorter than the dataclass.
CONTENT_COLUMNS: Final[tuple[str, ...]] = (
    "content_id",
    "title",
    "genre_id",
    "content_type",
    "runtime_minutes",
    "release_year",
    "language",
    "age_rating",
    "popularity_score",
    "season_count",
    "episode_count",
    "is_original",
    "added_on",
)

#: ``core.sessions``.
SESSION_COLUMNS: Final[tuple[str, ...]] = (
    "session_id",
    "user_id",
    "device_id",
    "session_start",
    "session_end",
    "duration_seconds",
    "event_count",
    "watch_seconds",
    "is_first_session",
    "entry_screen",
    "exit_screen",
)

#: ``core.events``. ``event_id`` is deliberately absent — PostgreSQL assigns it
#: from the sequence, so the seeder never has to coordinate a global counter
#: across batches.
EVENT_COLUMNS: Final[tuple[str, ...]] = (
    "session_id",
    "user_id",
    "content_id",
    "event_time",
    "event_name",
    "screen",
    "step_index",
    "watch_seconds",
    "progress_pct",
    "properties",
)

#: ``core.subscriptions``.
SUBSCRIPTION_COLUMNS: Final[tuple[str, ...]] = (
    "subscription_id",
    "user_id",
    "plan_id",
    "started_on",
    "ended_on",
    "status",
    "billing_period",
    "mrr_usd",
    "cancel_reason",
    "is_trial_conversion",
)

#: ``core.experiments``.
EXPERIMENT_COLUMNS: Final[tuple[str, ...]] = (
    "experiment_id",
    "key",
    "name",
    "hypothesis",
    "primary_metric",
    "variants",
    "traffic_allocation",
    "started_on",
    "ended_on",
    "status",
)

#: ``core.experiment_assignments``.
ASSIGNMENT_COLUMNS: Final[tuple[str, ...]] = (
    "experiment_id",
    "user_id",
    "variant",
    "assigned_at",
)

#: Tables the seeder owns, in a valid truncation order (children first).
#:
#: The six dimension tables are absent on purpose: Alembic revision 0002 owns
#: their rows, and truncating them would break the API's filter validation while
#: leaving the schema apparently intact.
TRUNCATE_ORDER: Final[tuple[str, ...]] = (
    "core.experiment_assignments",
    "core.experiments",
    "core.events",
    "core.subscriptions",
    "core.sessions",
    "core.content",
    "core.users",
)

#: Tables whose sequence must be advanced after loading explicit keys, paired
#: with the key column.
SEQUENCE_TABLES: Final[tuple[tuple[str, str], ...]] = (
    ("core.users", "user_id"),
    ("core.content", "content_id"),
    ("core.sessions", "session_id"),
    ("core.subscriptions", "subscription_id"),
    ("core.experiments", "experiment_id"),
)


def content_row(row: ContentRow) -> tuple[object, ...]:
    """Render a catalogue entry as a ``core.content`` row.

    Lives here rather than on :class:`~seeder.catalog.ContentRow` because it is a
    persistence concern, and because the dataclass carries one derived field
    (``total_runtime_minutes``) that must not be written.

    Args:
        row: The generated catalogue entry.

    Returns:
        Values in :data:`CONTENT_COLUMNS` order.
    """
    return (
        row.content_id,
        row.title,
        row.genre_id,
        row.content_type,
        row.runtime_minutes,
        row.release_year,
        row.language,
        row.age_rating,
        row.popularity_score,
        row.season_count,
        row.episode_count,
        row.is_original,
        row.added_on,
    )


def _prepare(value: Any) -> Any:  # noqa: ANN401 - accepts any column value
    """Adapt one Python value for ``COPY``.

    Only ``dict`` needs handling: psycopg has no implicit ``dict`` to ``jsonb``
    adapter, by design, because the mapping is ambiguous between ``json`` and
    ``jsonb``. Everything else psycopg dumps correctly on its own.

    Args:
        value: The column value.

    Returns:
        The value, wrapped if it needs an explicit adapter.
    """
    if isinstance(value, dict):
        return Jsonb(value)
    return value


class CopyLoader:
    """Streams rows into one table over a single ``COPY`` command.

    Used as a context manager so the ``COPY`` is always closed, including on an
    exception, which matters because an unclosed ``COPY`` leaves the connection
    unusable rather than merely failing the statement.

    Example:
        >>> with CopyLoader(conn, "core.events", EVENT_COLUMNS) as loader:
        ...     for row in rows:
        ...         loader.write(row.as_row())
    """

    __slots__ = ("_columns", "_conn", "_copy", "_ctx", "_rows", "_started", "_table")

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        table: str,
        columns: Sequence[str],
    ) -> None:
        """Initialise the loader.

        Args:
            conn: An open psycopg connection.
            table: Schema-qualified target table.
            columns: Column names, in the order rows will be written.
        """
        self._conn = conn
        self._table = table
        self._columns = tuple(columns)
        self._rows = 0
        self._started = 0.0
        self._ctx: Any = None
        self._copy: Any = None

    def __enter__(self) -> CopyLoader:
        """Open the ``COPY`` stream.

        Returns:
            This loader.
        """
        column_list = ", ".join(self._columns)
        statement = f"COPY {self._table} ({column_list}) FROM STDIN"
        self._started = time.perf_counter()
        self._ctx = self._conn.cursor().copy(statement)
        self._copy = self._ctx.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Close the ``COPY`` stream and log throughput.

        Args:
            exc_type: Exception class, if one is propagating.
            exc: Exception instance, if any.
            tb: Traceback, if any.
        """
        if self._ctx is not None:
            self._ctx.__exit__(exc_type, exc, tb)

        if exc_type is None:
            elapsed = time.perf_counter() - self._started
            logger.info(
                "copy_complete",
                table=self._table,
                rows=self._rows,
                seconds=round(elapsed, 2),
                rows_per_second=int(self._rows / elapsed) if elapsed > 0 else 0,
            )

    def write(self, row: Sequence[Any]) -> None:
        """Write one row.

        Args:
            row: Values in the column order given to the constructor.
        """
        self._copy.write_row(tuple(_prepare(value) for value in row))
        self._rows += 1

    def write_all(self, rows: Iterable[Sequence[Any]]) -> int:
        """Write many rows.

        Args:
            rows: Row tuples.

        Returns:
            The number of rows written by this call.
        """
        before = self._rows
        for row in rows:
            self.write(row)
        return self._rows - before

    @property
    def rows_written(self) -> int:
        """Return the total rows written so far."""
        return self._rows


def connect(dsn: str) -> psycopg.Connection[Any]:
    """Open a connection tuned for bulk loading.

    Args:
        dsn: A libpq connection string or URL.

    Returns:
        An open connection with autocommit disabled, so the whole load is one
        transaction and a failure leaves no partial dataset behind.
    """
    conn = psycopg.connect(dsn, autocommit=False)

    with conn.cursor() as cur:
        # synchronous_commit=off trades durability on crash for throughput. Correct
        # here and nowhere near production: this data is regenerable by definition,
        # and the setting is session-scoped so it cannot leak.
        cur.execute("SET synchronous_commit = off")
        # Generous, because index maintenance during COPY is the bottleneck.
        cur.execute("SET maintenance_work_mem = '512MB'")
        cur.execute("SET work_mem = '64MB'")
        cur.execute("SET application_name = 'prism-seeder'")
        # The seeder writes UTC-aware timestamps; pinning removes any doubt about
        # how a naive value would be interpreted.
        cur.execute("SET TIME ZONE 'UTC'")

    return conn


def truncate(conn: psycopg.Connection[Any]) -> None:
    """Empty every table the seeder owns.

    ``RESTART IDENTITY`` resets the sequences, and ``CASCADE`` follows the foreign
    keys — needed because ``core.events`` references ``core.sessions``, which
    references ``core.users``.

    The six dimension tables are untouched. Their rows come from Alembic revision
    0002, and clearing them would silently break the API's filter validation.

    Args:
        conn: An open connection.
    """
    targets = ", ".join(TRUNCATE_ORDER)
    with conn.cursor() as cur:
        logger.info("truncating", tables=len(TRUNCATE_ORDER))
        cur.execute(f"TRUNCATE {targets} RESTART IDENTITY CASCADE")


def ensure_partitions(
    conn: psycopg.Connection[Any],
    window_start: date,
    window_end: date,
) -> int:
    """Create any missing monthly partition of ``core.events``.

    Revision 0004 pre-creates a wide range, so this is normally a no-op. It exists
    for the case where ``PRISM_SEED__WINDOW_END`` is set to a date outside that
    range: without it those rows would land in ``core.events_default``, which the
    tests assert stays empty.

    Args:
        conn: An open connection.
        window_start: First day of the simulation window.
        window_end: Last day of the simulation window.

    Returns:
        Number of months checked.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT core.ensure_events_partition(month::date)
            FROM generate_series(
                date_trunc('month', %s::date),
                date_trunc('month', %s::date) + interval '1 month',
                interval '1 month'
            ) AS month
            """,
            (window_start, window_end),
        )
        created = cur.fetchall()

    logger.info("partitions_ensured", months=len(created))
    return len(created)


def reset_sequences(conn: psycopg.Connection[Any]) -> None:
    """Advance each sequence past the highest loaded key.

    Required because most tables are loaded with explicit surrogate keys, leaving
    their sequences at zero. Skipping this is the classic post-bulk-load bug: the
    data looks perfect until the first ordinary ``INSERT`` fails on a duplicate key.

    Args:
        conn: An open connection.
    """
    with conn.cursor() as cur:
        for table, column in SEQUENCE_TABLES:
            # pg_get_serial_sequence resolves the sequence name from the catalogue
            # rather than assuming the "<table>_<column>_seq" convention.
            cur.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence(%s, %s),
                    COALESCE((SELECT MAX({column}) FROM {table}), 1),
                    true
                )
                """,
                (table, column),
            )

        # core.events assigns event_id from its own sequence during COPY, so it is
        # already correct; setting it again would be harmless but misleading.
        logger.info("sequences_reset", tables=len(SEQUENCE_TABLES))


def analyze(conn: psycopg.Connection[Any]) -> None:
    """Refresh planner statistics on every loaded table.

    Without this the planner still believes the tables are empty and will choose
    nested loops over 3.4 million rows, which makes a freshly seeded database feel
    broken. ``ANALYZE`` cannot run inside the load transaction's snapshot usefully,
    so the caller commits first.

    Args:
        conn: An open connection.
    """
    with conn.cursor() as cur:
        for table in reversed(TRUNCATE_ORDER):
            cur.execute(f"ANALYZE {table}")
    logger.info("analyze_complete", tables=len(TRUNCATE_ORDER))


def refresh_analytics(conn: psycopg.Connection[Any]) -> list[tuple[str, float]]:
    """Populate the analytics materialized views.

    Called with ``concurrent => false``: the views were created ``WITH NO DATA`` by
    revision 0006, and PostgreSQL rejects ``REFRESH CONCURRENTLY`` on a view that
    has never been populated. Subsequent refreshes via ``make refresh`` use the
    concurrent path.

    Args:
        conn: An open connection.

    Returns:
        ``(view_name, duration_ms)`` per view.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT view_name, duration_ms FROM analytics.refresh_all(false)")
        results = [(name, float(duration)) for name, duration in cur.fetchall()]

    for name, duration in results:
        logger.info("mv_refreshed", view=name, ms=round(duration, 1))

    return results


def row_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """Return exact row counts for every seeded table.

    Exact rather than the ``pg_stat_user_tables`` estimate, because these numbers
    are reported to the user as the result of their seed run and an estimate that
    disagrees with reality by a few per cent reads as a bug.

    Args:
        conn: An open connection.

    Returns:
        Mapping of unqualified table name to row count.
    """
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for qualified in reversed(TRUNCATE_ORDER):
            cur.execute(f"SELECT count(*) FROM {qualified}")
            row = cur.fetchone()
            counts[qualified.split(".", 1)[1]] = int(row[0]) if row else 0
    return counts


def default_partition_count(conn: psycopg.Connection[Any]) -> int:
    """Return the row count of ``core.events_default``.

    Expected to be zero. A non-zero value means some event fell outside every
    declared monthly partition, which is a boundary bug worth surfacing rather
    than leaving for someone to find later.

    Args:
        conn: An open connection.

    Returns:
        Rows in the default partition.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.events_default")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def read_dimensions(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Read every dimension table the generators need.

    The generators reference dimension rows by name and need their surrogate keys,
    so this is the bridge between the migration's reference data and the
    simulation.

    Args:
        conn: An open connection.

    Returns:
        A mapping with keys ``country_ids``, ``country_tiers``, ``device_ids``,
        ``device_form_factors``, ``channel_ids``, ``persona_ids``,
        ``persona_bases``, ``genre_ids``, ``genre_names``, ``plan_ids``,
        ``plan_prices`` and ``plan_names``.

    Raises:
        RuntimeError: If a dimension table is empty, which means migrations have
            not been applied.
    """
    result: dict[str, Any] = {}

    with conn.cursor() as cur:
        cur.execute("SELECT country_id, name, tier FROM core.countries")
        countries = cur.fetchall()
        result["country_ids"] = {name: cid for cid, name, _ in countries}
        result["country_tiers"] = {name: int(tier) for _, name, tier in countries}

        cur.execute("SELECT device_id, name, form_factor FROM core.devices")
        devices = cur.fetchall()
        result["device_ids"] = {name: did for did, name, _ in devices}
        result["device_form_factors"] = {did: form for did, _, form in devices}

        cur.execute("SELECT channel_id, name FROM core.marketing_channels")
        result["channel_ids"] = dict((name, cid) for cid, name in cur.fetchall())

        cur.execute(
            """
            SELECT persona_id, name, base_sessions_per_week,
                   base_completion_rate, base_churn_propensity
            FROM core.personas
            """
        )
        personas = cur.fetchall()
        result["persona_ids"] = {name: pid for pid, name, _, _, _ in personas}
        result["persona_bases"] = {
            name: (float(sessions), float(completion), float(churn))
            for _, name, sessions, completion, churn in personas
        }

        cur.execute("SELECT genre_id, name FROM core.genres")
        genres = cur.fetchall()
        result["genre_ids"] = {name: gid for gid, name in genres}
        result["genre_names"] = {gid: name for gid, name in genres}

        cur.execute(
            "SELECT plan_id, name, monthly_price_usd FROM core.subscription_plans"
        )
        plans = cur.fetchall()
        result["plan_ids"] = {name: pid for pid, name, _ in plans}
        result["plan_prices"] = {name: float(price) for _, name, price in plans}
        result["plan_names"] = {pid: name for pid, name, _ in plans}

    for key in ("country_ids", "device_ids", "channel_ids", "persona_ids", "genre_ids"):
        if not result[key]:
            raise RuntimeError(
                f"core dimension table for {key!r} is empty. "
                "Run `alembic upgrade head` (or `make migrate`) before seeding."
            )

    return result


def batched(rows: Iterable[Any], size: int) -> Iterator[list[Any]]:
    """Yield fixed-size chunks from an iterable.

    Used to bound peak memory when writing events: the generator can produce far
    more rows than should be held at once.

    Args:
        rows: Source iterable.
        size: Maximum chunk length.

    Yields:
        Lists of at most ``size`` items.
    """
    chunk: list[Any] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


__all__ = [
    "ASSIGNMENT_COLUMNS",
    "CONTENT_COLUMNS",
    "EVENT_COLUMNS",
    "EXPERIMENT_COLUMNS",
    "SEQUENCE_TABLES",
    "SESSION_COLUMNS",
    "SUBSCRIPTION_COLUMNS",
    "TRUNCATE_ORDER",
    "USER_COLUMNS",
    "CopyLoader",
    "analyze",
    "batched",
    "connect",
    "content_row",
    "default_partition_count",
    "ensure_partitions",
    "read_dimensions",
    "refresh_analytics",
    "reset_sequences",
    "row_counts",
    "truncate",
]
