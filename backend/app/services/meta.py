"""Service metadata: filter options, dataset extent, health, analytics refresh.

The only service module that answers questions about the *service* rather than about the
data. Four functions, and they cache differently from each other for reasons worth
stating.

Nothing here takes filters. These figures describe the dataset as a whole, and a
per-country event count would be a different question — answering it from an endpoint
named "bounds" would invite a reader to mistake a segment's extent for the dataset's.

Why the bounds endpoint exists
------------------------------
The window parameters in :mod:`app.schemas.params` deliberately have no defaults. A "last
30 days" default would open every chart empty on a repository cloned months after its
data was generated, and an empty chart reads as a broken service rather than as a badly
chosen window. A client reads :func:`get_dataset_bounds` first, then picks a window inside
what it reports.

Caching, per function
---------------------
:func:`get_filter_options` does not touch the database at all — the catalogue is already
in memory, loaded once at startup — so caching it would add a serialisation round trip to
a dictionary lookup.

:func:`get_dataset_bounds` is cached on the shortest band. It is nearly free to compute,
but it is polled by every client on load, and the shortest band still means a refresh
becomes visible within a minute rather than being pinned for an hour.

:func:`get_health` is never cached. A cached health check reports the state of the world
as it was, which is precisely the thing a health check must not do — an orchestrator
would keep routing traffic to a process whose database had gone away.

:func:`refresh_analytics` writes, so caching does not apply. It is the one function in the
services layer that is not read-only.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import sqlalchemy

from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import autocommit_connection, healthcheck
from app.repositories import meta as repo
from app.services.base import Ttl, cached_rows

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.deps import DimensionCatalog

logger = get_logger(__name__)

#: Cache namespace for this module.
NAMESPACE = "meta"

#: Status values reported by :func:`get_health`.
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_ERROR = "error"

#: The refresh call. Not in ``app/sql/queries/`` with the other statements: that tree is
#: read-only analytics loaded by :mod:`app.sql.registry`, and putting a statement that
#: rebuilds materialized views alongside them would make the registry a mix of reads and
#: writes. One writing statement in the one writing function is easier to audit.
_REFRESH_SQL = sqlalchemy.text(
    "SELECT view_name, duration_ms FROM analytics.refresh_all(:concurrent)"
)


def get_filter_options(catalog: DimensionCatalog) -> dict[str, list[str]]:
    """Return every filter's valid values, for the dashboard's filter bar.

    Reads the in-memory catalogue loaded at startup; issues no query. The dashboard
    populates its multi-selects from this rather than hard-coding them, so a dimension
    row added by a migration appears in the UI without a frontend change.

    Args:
        catalog: The loaded dimension catalogue.

    Returns:
        Mapping of filter name to its sorted valid values, matching the fields of
        :class:`~app.schemas.meta.FilterOptions`.
    """
    return catalog.options()


async def get_dataset_bounds(session: AsyncSession) -> dict[str, Any]:
    """Return the span and size of the seeded dataset.

    Runs the two queries in :mod:`app.repositories.meta` in order, and the order is
    load-bearing: ``meta/activity_bounds`` reads a materialized view, and reading one
    that was created ``WITH NO DATA`` raises rather than returning nothing. So the
    counts query — which touches only ``core`` and the system catalogue — is consulted
    first, and the bounds query is skipped entirely when it reports the views as
    unpopulated. Without that guard this function would return a 503 in exactly the
    situation it exists to describe.

    Args:
        session: A read-only session.

    Returns:
        A mapping matching :class:`~app.schemas.meta.DatasetBounds`. When the analytics
        views are unpopulated the three date fields are ``None`` and ``is_seeded`` is
        ``False``, while ``users`` and ``events`` still report what is in ``core`` — a
        database with users but no refreshed views is a real and recoverable state, and
        reporting zeros for everything would hide which half is missing.

    Raises:
        DatabaseError: If the database is unreachable.
    """

    async def produce() -> dict[str, Any]:
        """Read the counts, then the bounds if the views can be read at all."""
        counts = await repo.get_dataset_counts(session)
        bounds: dict[str, Any] = {}
        if counts.get("analytics_ready"):
            bounds = await repo.get_activity_bounds(session)

        first = bounds.get("first_activity_date")
        return {
            "first_activity_date": first,
            "last_activity_date": bounds.get("last_activity_date"),
            "days": bounds.get("days"),
            "users": counts.get("users", 0),
            "events": counts.get("approx_events", 0),
            # Populated-but-empty is a third state, distinct from unpopulated: the views
            # exist and can be read, they simply hold no activity. Both mean "not
            # seeded" to a client, so `is_seeded` keys off an actual date rather than
            # off `analytics_ready`.
            "is_seeded": first is not None,
        }

    return await cached_rows(NAMESPACE, "dataset_bounds", {}, produce, ttl=Ttl.KPI)


async def get_health() -> dict[str, Any]:
    """Probe the service and its dependencies.

    Never cached — see the module docstring.

    Three states rather than a boolean, because the middle one is the only one with an
    actionable fix: ``degraded`` means the database is reachable but not ready, which is
    almost always a database that was migrated and never seeded. Collapsing that into
    "unhealthy" would send someone looking for a connectivity problem that does not
    exist.

    Returns:
        A mapping matching :class:`~app.schemas.meta.HealthStatus`.
    """
    settings = get_settings()
    probe = await healthcheck()

    connected = bool(probe.get("connected"))
    schema_ready = bool(probe.get("schema_ready"))
    analytics_ready = bool(probe.get("analytics_ready"))

    if not connected:
        status = STATUS_ERROR
        detail = probe.get("error") or "Database unreachable."
    elif not schema_ready:
        status = STATUS_DEGRADED
        detail = "Database reachable but not migrated. Run `make migrate`."
    elif not analytics_ready:
        status = STATUS_DEGRADED
        detail = "Migrated but analytics views are empty. Run `make seed`."
    else:
        status = STATUS_OK
        detail = None

    return {
        "status": status,
        "version": settings.api.version,
        "environment": str(settings.env),
        "database_connected": connected,
        "schema_ready": schema_ready,
        "analytics_ready": analytics_ready,
        # Which backend is actually serving, not which was configured. Redis is optional
        # and the local LRU is a deliberate fallback rather than a failure, so a `local`
        # reading with Redis enabled means the fallback engaged — worth being able to see.
        "cache_backend": get_cache().name,
        "detail": detail,
    }


async def refresh_analytics(*, concurrent: bool = True) -> dict[str, Any]:
    """Rebuild every analytics materialized view.

    The one write path in the services layer. Delegates to ``analytics.refresh_all``,
    which refreshes each view in dependency order and ``ANALYZE``s it.

    Runs on an autocommit connection because ``REFRESH MATERIALIZED VIEW CONCURRENTLY``
    cannot execute inside a transaction block — :func:`app.db.session.autocommit_connection`
    exists for this caller.

    Args:
        concurrent: Whether to refresh concurrently. Concurrent refresh keeps the
            dashboard serving the previous snapshot instead of taking an exclusive lock,
            at roughly twice the runtime. The database downgrades this to a
            non-concurrent refresh per view that has never been populated, since
            ``CONCURRENTLY`` is impossible there — so the first refresh after a
            migration succeeds rather than failing with what looks like a bug.

    Returns:
        A mapping matching :class:`~app.schemas.meta.RefreshResult`.

    Raises:
        DatabaseError: On any driver failure.
    """
    started = time.perf_counter()
    async with autocommit_connection() as conn:
        result = await conn.execute(_REFRESH_SQL, {"concurrent": concurrent})
        rows = result.mappings().all()

    duration = time.perf_counter() - started
    refreshed = [str(row["view_name"]) for row in rows]
    logger.info(
        "analytics_refreshed",
        views=len(refreshed),
        duration_seconds=round(duration, 2),
        concurrent=concurrent,
    )

    return {
        "refreshed": refreshed,
        "duration_seconds": round(duration, 3),
        "concurrent": concurrent,
        "detail": (
            f"Refreshed {len(refreshed)} materialized view(s) in {duration:.1f}s"
            f"{' concurrently' if concurrent else ''}."
        ),
    }


__all__ = [
    "NAMESPACE",
    "STATUS_DEGRADED",
    "STATUS_ERROR",
    "STATUS_OK",
    "get_dataset_bounds",
    "get_filter_options",
    "get_health",
    "refresh_analytics",
]
