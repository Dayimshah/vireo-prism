"""Async database engine and session factory.

One engine per process, created during the FastAPI lifespan and disposed on
shutdown. Nothing here is imported at module scope by request handlers; they take
a session from :mod:`app.db.deps` instead, so the engine's lifetime is owned by
the application rather than by import order.

Read-only by design
-------------------
Every session this module hands out is opened in a read-only transaction. The API
serves analytics: the only endpoint that changes anything is
``POST /admin/refresh``, which calls ``analytics.refresh_all()`` through a
separate escape hatch (:func:`writable_session`). Setting
``default_transaction_read_only`` means a stray ``UPDATE`` anywhere in the
service layer fails at the database rather than being caught in review, which is
a stronger guarantee than a convention.

Statement timeout
-----------------
Applied per connection from ``PRISM_DB__STATEMENT_TIMEOUT_MS``. An analytical
query with a pathological filter combination is cancelled by PostgreSQL instead
of holding a worker until the client gives up. :class:`QueryTimeoutError`
translates that into a 504 with actionable advice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.exceptions import DatabaseError, QueryTimeoutError, StaleAnalyticsError
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level engine state
#
# Deliberately private with accessor functions rather than a bare module global:
# the accessor raises a clear error if something reaches for the engine before
# the lifespan created it, which is a much better failure than `None.connect()`.
# ---------------------------------------------------------------------------
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

#: PostgreSQL error codes worth translating into specific application errors.
#: Everything else becomes a generic :class:`DatabaseError`.
_PG_QUERY_CANCELED: Final[str] = "57014"
_PG_OBJECT_NOT_IN_PREREQUISITE_STATE: Final[str] = "55000"
_PG_UNDEFINED_TABLE: Final[str] = "42P01"
_PG_INSUFFICIENT_RESOURCES: Final[str] = "53000"
_PG_CONNECTION_FAILURE: Final[str] = "08006"


def _server_settings() -> dict[str, str]:
    """Return per-connection PostgreSQL settings.

    asyncpg applies these at connection time, so they cover every statement on
    the connection without a round trip per query.

    Returns:
        Mapping of PostgreSQL parameter names to values.
    """
    settings = get_settings()
    return {
        # Cancel runaway analytical queries server-side.
        "statement_timeout": str(settings.db.statement_timeout_ms),
        # A read-only API should never hold a lock; fail fast if it somehow tries.
        "lock_timeout": "5000",
        # Cheap insurance against an abandoned transaction pinning MVCC state and
        # blocking REFRESH MATERIALIZED VIEW CONCURRENTLY.
        "idle_in_transaction_session_timeout": "60000",
        # Named so slow queries in pg_stat_activity are attributable.
        "application_name": f"prism-api/{settings.api.version}",
        # Every date derivation in the SQL pins UTC explicitly, but setting it
        # here removes any doubt about what a bare ::date cast would do.
        "timezone": "UTC",
        # Analytics reads are wide; encourage the planner to parallelise.
        "max_parallel_workers_per_gather": "4",
    }


def create_engine() -> AsyncEngine:
    """Build the async engine.

    Returns:
        A configured :class:`AsyncEngine`. Not stored; :func:`init_engine` owns
        the process-wide instance.
    """
    settings = get_settings()

    # In tests, pooling across event loops causes "attached to a different loop"
    # errors that look like application bugs. NullPool sidesteps it entirely and
    # the cost is irrelevant at test volumes.
    use_pool = settings.env.value != "test"

    engine = create_async_engine(
        settings.db.async_dsn,
        echo=settings.db.echo_sql,
        future=True,
        # Analytical result sets are large; orjson via SQLAlchemy is not
        # applicable to asyncpg, so JSONB decoding stays with the driver.
        pool_pre_ping=True,
        **(
            {
                "pool_size": settings.db.pool_size,
                "max_overflow": settings.db.max_overflow,
                "pool_timeout": settings.db.pool_timeout_seconds,
                # Recycle before any proxy or Postgres idle timeout can close a
                # connection underneath us.
                "pool_recycle": settings.db.pool_recycle_seconds,
            }
            if use_pool
            else {"poolclass": NullPool}
        ),
        connect_args={
            "server_settings": _server_settings(),
            "timeout": settings.db.pool_timeout_seconds,
            # Disable asyncpg's prepared-statement cache. Partitioned tables can
            # invalidate cached plans when a partition is added, producing
            # InvalidCachedStatementError on an otherwise valid query.
            "statement_cache_size": 0,
            # Cloud databases (Neon, Supabase, RDS) require TLS. asyncpg uses
            # a boolean `ssl` argument rather than the libpq sslmode string.
            **({"ssl": True} if settings.db.sslmode == "require" else {}),
        },
    )

    _register_engine_events(engine)
    return engine


def _register_engine_events(engine: AsyncEngine) -> None:
    """Attach diagnostic listeners to the engine.

    Args:
        engine: The engine to instrument.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:  # noqa: ANN401
        """Log new physical connections at debug level."""
        del dbapi_connection, connection_record
        logger.debug("db_connection_established")

    @event.listens_for(engine.sync_engine, "invalidate")
    def _on_invalidate(dbapi_connection: Any, connection_record: Any, exception: Any) -> None:  # noqa: ANN401
        """Warn when a pooled connection is discarded."""
        del dbapi_connection, connection_record
        logger.warning("db_connection_invalidated", error=str(exception))


async def init_engine() -> AsyncEngine:
    """Create the process-wide engine and verify connectivity.

    Called once from the FastAPI lifespan. Failing here rather than on the first
    request means a misconfigured database surfaces at boot, where it is obvious.

    Returns:
        The initialised engine.

    Raises:
        DatabaseError: If the database cannot be reached.
    """
    global _engine, _sessionmaker  # noqa: PLW0603 - process-wide singletons

    if _engine is not None:
        return _engine

    settings = get_settings()
    engine = create_engine()

    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version()"))).scalar_one()
            migrated = (
                await conn.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'core' AND table_name = 'events')"
                    )
                )
            ).scalar_one()
    except SQLAlchemyError as exc:
        await engine.dispose()
        logger.error("db_connect_failed", dsn=settings.db.safe_dsn, error=str(exc))
        raise DatabaseError(
            f"Cannot connect to the database at {settings.db.safe_dsn}. "
            "Is PostgreSQL running? Try `docker compose up -d postgres`."
        ) from exc

    _engine = engine
    _sessionmaker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        # Nothing in the read path writes, so flushing would be pure overhead.
        autobegin=True,
    )

    logger.info(
        "db_engine_ready",
        dsn=settings.db.safe_dsn,
        server_version=str(version).split(" ")[1] if version else "unknown",
        pool_size=settings.db.pool_size,
        statement_timeout_ms=settings.db.statement_timeout_ms,
        schema_present=bool(migrated),
    )

    if not migrated:
        logger.warning(
            "db_schema_missing",
            hint="core.events not found. Run `alembic upgrade head` or `make migrate`.",
        )

    return engine


async def dispose_engine() -> None:
    """Close every pooled connection and clear engine state.

    Called from the lifespan shutdown hook. Idempotent.
    """
    global _engine, _sessionmaker  # noqa: PLW0603 - process-wide singletons

    if _engine is not None:
        await _engine.dispose()
        logger.info("db_engine_disposed")

    _engine = None
    _sessionmaker = None


def get_engine() -> AsyncEngine:
    """Return the process-wide engine.

    Returns:
        The initialised engine.

    Raises:
        RuntimeError: If called before :func:`init_engine`.
    """
    if _engine is None:
        raise RuntimeError(
            "Database engine not initialised. init_engine() runs in the FastAPI "
            "lifespan; call it directly in scripts and tests."
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory.

    Returns:
        The configured session factory.

    Raises:
        RuntimeError: If called before :func:`init_engine`.
    """
    if _sessionmaker is None:
        raise RuntimeError(
            "Session factory not initialised. init_engine() runs in the FastAPI "
            "lifespan; call it directly in scripts and tests."
        )
    return _sessionmaker


def translate_db_error(exc: SQLAlchemyError) -> Exception:
    """Map a driver exception onto an application error.

    Raw driver messages can carry schema names, SQL fragments and occasionally
    parameter values. Translating here means the client sees a stable, safe
    problem document while the original is logged in full.

    Args:
        exc: The exception raised by SQLAlchemy or the driver.

    Returns:
        The application error to raise in its place.
    """
    settings = get_settings()
    code: str | None = None

    if isinstance(exc, DBAPIError):
        code = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
            getattr(exc, "orig", None), "pgcode", None
        )

    if code == _PG_QUERY_CANCELED:
        return QueryTimeoutError(settings.db.statement_timeout_ms)

    # A materialized view that has never been refreshed raises 55000 on SELECT.
    # That is the "migrated but never seeded" case, and deserves its own message.
    if code == _PG_OBJECT_NOT_IN_PREREQUISITE_STATE:
        message = str(exc.orig if isinstance(exc, DBAPIError) else exc)
        view = next(
            (
                name
                for name in (
                    "mv_user_daily",
                    "mv_content_daily",
                    "mv_user_lifetime",
                    "mv_funnel_steps",
                )
                if name in message
            ),
            None,
        )
        return StaleAnalyticsError(view)

    if code == _PG_UNDEFINED_TABLE:
        return DatabaseError(
            "A required table or view is missing. Run `make migrate` to apply migrations."
        )

    if code in {_PG_INSUFFICIENT_RESOURCES, _PG_CONNECTION_FAILURE} or isinstance(
        exc, OperationalError
    ):
        return DatabaseError()

    return DatabaseError()


@asynccontextmanager
async def read_session() -> AsyncIterator[AsyncSession]:
    """Yield a read-only session outside a request context.

    For scripts, tests and background jobs. Request handlers use
    :func:`app.db.deps.get_session` instead, which is the same thing wired into
    FastAPI's dependency system.

    Yields:
        A session in a read-only transaction.

    Raises:
        DatabaseError: Translated from any driver failure.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            yield session
        except SQLAlchemyError as exc:
            logger.error("db_session_error", error=str(exc), exc_info=True)
            raise translate_db_error(exc) from exc
        finally:
            # Read-only work has nothing to commit, and rolling back returns the
            # connection to the pool without a lingering snapshot.
            await session.rollback()


@asynccontextmanager
async def writable_session() -> AsyncIterator[AsyncSession]:
    """Yield a writable session.

    The single escape hatch from this module's read-only default. Used only by
    ``POST /admin/refresh`` to call ``analytics.refresh_all()``, and by the test
    fixtures that build a schema.

    Yields:
        A session that may write.

    Raises:
        DatabaseError: Translated from any driver failure. The transaction is
            rolled back first.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.error("db_write_session_error", error=str(exc), exc_info=True)
            raise translate_db_error(exc) from exc


@asynccontextmanager
async def autocommit_connection() -> AsyncIterator[AsyncConnection]:
    """Yield a connection outside any transaction.

    ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` cannot run inside a transaction
    block, so the admin refresh endpoint needs a connection in autocommit mode
    rather than a session.

    Yields:
        A connection with ``AUTOCOMMIT`` isolation.

    Raises:
        DatabaseError: Translated from any driver failure.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        try:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            yield conn
        except SQLAlchemyError as exc:
            logger.error("db_autocommit_error", error=str(exc), exc_info=True)
            raise translate_db_error(exc) from exc


async def healthcheck() -> dict[str, Any]:
    """Probe the database for the readiness endpoint.

    Reports whether the schema is migrated and whether the analytics views hold
    data, because "connected but empty" is a state a reader needs distinguished
    from "connected and ready".

    Returns:
        A mapping with ``connected``, ``schema_ready``, ``analytics_ready`` and,
        on failure, ``error``.
    """
    result: dict[str, Any] = {
        "connected": False,
        "schema_ready": False,
        "analytics_ready": False,
    }
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            result["connected"] = True

            result["schema_ready"] = bool(
                (
                    await conn.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = 'core' AND table_name = 'events')"
                        )
                    )
                ).scalar_one()
            )

            # relispopulated is false for a materialized view created WITH NO DATA
            # and never refreshed — precisely the unseeded state.
            result["analytics_ready"] = bool(
                (
                    await conn.execute(
                        text(
                            "SELECT COALESCE(bool_and(c.relispopulated), false) "
                            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'analytics' AND c.relkind = 'm'"
                        )
                    )
                ).scalar_one()
            )
    except (SQLAlchemyError, RuntimeError) as exc:
        result["error"] = str(exc)
        logger.warning("db_healthcheck_failed", error=str(exc))

    return result


__all__ = [
    "autocommit_connection",
    "create_engine",
    "dispose_engine",
    "get_engine",
    "get_sessionmaker",
    "healthcheck",
    "init_engine",
    "read_session",
    "translate_db_error",
    "writable_session",
]
