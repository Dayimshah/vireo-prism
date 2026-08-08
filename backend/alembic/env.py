"""Alembic migration environment for the Prism analytics warehouse.

Two deliberate choices are worth calling out, because both are the sort of thing
a reviewer checks first:

**The database URL is not in ``alembic.ini``.** It is read from
:mod:`app.core.config` at runtime, so credentials exist only in the environment
and the DSN has exactly one source of truth shared with the API.

**Autogenerate ignores the ``analytics`` schema.** That schema contains only
materialized views, which SQLAlchemy's metadata cannot model. Left unfiltered,
every ``--autogenerate`` run would helpfully propose dropping all four of them.
:func:`include_object` and :func:`include_name` prevent that.

The synchronous ``psycopg`` driver is used here rather than ``asyncpg``: DDL is a
one-shot operation with no concurrency to gain from, and a sync engine keeps this
file readable.
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import TYPE_CHECKING, Any, Final

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.models import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.sql.schema import SchemaItem

# ---------------------------------------------------------------------------
# Alembic wiring
# ---------------------------------------------------------------------------

config = context.config

# Only configure logging from alembic.ini when Alembic is invoked directly. When
# migrations run inside the API container, app.core.logging has already
# installed the structlog pipeline and this would tear it down.
if config.config_file_name is not None and not context.is_offline_mode():
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.db.sync_dsn)

#: Metadata used for ``--autogenerate`` comparison.
target_metadata = Base.metadata

#: Schemas Alembic is allowed to manage. ``analytics`` is created and populated
#: by revision 0006 by hand and is intentionally invisible to autogenerate.
MANAGED_SCHEMAS: Final[frozenset[str]] = frozenset({"core"})

#: Schemas excluded from reflection entirely.
IGNORED_SCHEMAS: Final[frozenset[str]] = frozenset(
    {"analytics", "information_schema", "pg_catalog", "pg_toast"}
)


def include_name(
    name: str | None,
    type_: str,
    _parent_names: dict[str, str | None],
) -> bool:
    """Filter which schemas and tables Alembic reflects.

    Args:
        name: Object name, or ``None`` for the default schema.
        type_: Alembic object category, e.g. ``"schema"`` or ``"table"``.
        _parent_names: Enclosing object names; unused.

    Returns:
        True when the object should participate in reflection.
    """
    if type_ == "schema":
        # `None` is the default (public) schema, which holds alembic_version.
        return name is None or name in MANAGED_SCHEMAS
    return True


def include_object(
    object_: SchemaItem,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: SchemaItem | None,
) -> bool:
    """Filter which database objects autogenerate considers.

    Excludes the ``analytics`` schema (materialized views), the Alembic bookkeeping
    table, and the per-month partitions of ``core.events`` — those are managed
    explicitly by revision 0004 and must never be diffed against ORM metadata.

    Args:
        object_: The schema object under consideration.
        name: Its name.
        type_: Alembic object category.
        _reflected: Whether the object came from the database; unused.
        _compare_to: The counterpart being compared against; unused.

    Returns:
        True when the object should be included in the comparison.
    """
    schema = getattr(object_, "schema", None)
    if schema in IGNORED_SCHEMAS:
        return False
    if type_ == "table":
        if name == "alembic_version":
            return False
        # core.events_2026_03 and friends: partitions, not independent tables.
        if name is not None and name.startswith("events_"):
            return False
    return True


def _shared_context_kwargs() -> dict[str, Any]:
    """Return context options common to online and offline migration runs.

    Returns:
        Keyword arguments for :func:`alembic.context.configure`.
    """
    return {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "include_name": include_name,
        "include_object": include_object,
        # Detect column type changes and server-default changes, which Alembic
        # otherwise silently ignores.
        "compare_type": True,
        "compare_server_default": True,
        # Render batch-friendly ops and keep generated files deterministic.
        "render_as_batch": False,
        "version_table": "alembic_version",
        "version_table_schema": None,
    }


def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without connecting to a database.

    Useful for reviewing DDL in a pull request, or handing a script to a DBA:
    ``alembic upgrade head --sql > schema.sql``.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_shared_context_kwargs(),
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """Run migrations against an open connection.

    Args:
        connection: A live SQLAlchemy connection.
    """
    context.configure(connection=connection, **_shared_context_kwargs())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations.

    ``NullPool`` is used because the process exits immediately afterwards, so
    pooling would only delay shutdown.
    """
    section = config.get_section(config.config_ini_section, {})
    engine = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with engine.connect() as connection:
        _do_run_migrations(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
