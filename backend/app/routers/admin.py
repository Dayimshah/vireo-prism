"""Privileged operations. One endpoint, and the only write path in the API.

Every other route in this service runs inside a read-only transaction. This one rebuilds
the four analytics materialized views, so it is separated from them by prefix, by tag, and
by authentication: it requires the ``X-API-Key`` header, checked with a timing-safe
comparison in :func:`app.core.security.require_admin_key`.

Why a refresh needs an endpoint at all
--------------------------------------
The views are the reason most queries return in milliseconds rather than scanning a
65-partition event table. They are stale by definition between refreshes, so something has
to trigger one after a seed or a backfill. Doing it through the API keeps that trigger in
the same place as the readiness signal (``/health`` reports ``analytics_ready``), rather
than requiring a separate shell into the database.

This is a genuinely expensive operation — it rewrites every view and re-``ANALYZE``s it.
It is not idempotent in cost, only in outcome, and it is rate limited like everything else.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.security import AdminAuth
from app.routers.base import respond_value
from app.schemas import meta as schema
from app.schemas.base import ProblemDetail, ValueResponse, with_rate_limit
from app.services import meta as service

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    responses=with_rate_limit(
        {401: {"model": ProblemDetail, "description": "Missing or invalid X-API-Key"}}
    ),
)


@router.post(
    "/refresh-analytics",
    response_model=ValueResponse[schema.RefreshResult],
    summary="Rebuild the analytics materialized views",
)
async def refresh_analytics(
    _: AdminAuth,
    concurrent: Annotated[
        bool,
        Query(
            description=(
                "Use REFRESH MATERIALIZED VIEW CONCURRENTLY, which does not block "
                "readers. Must be false for the first refresh after a migration."
            ),
        ),
    ] = True,
) -> ValueResponse[schema.RefreshResult]:
    """Rebuild every analytics view in dependency order.

    Runs on an autocommit connection, because ``REFRESH MATERIALIZED VIEW CONCURRENTLY``
    cannot run inside a transaction block.

    ``concurrent=true`` keeps the views readable throughout, at the cost of needing a
    unique index and, more importantly, of requiring the view to have been populated at
    least once. The migrations create them ``WITH NO DATA``, so the **first** refresh on a
    fresh database must pass ``concurrent=false`` — a concurrent refresh of a
    never-populated view raises rather than falling back. The response echoes which mode
    ran, so this is visible after the fact rather than inferred.

    ``refreshed`` lists the views in the order the database reported rebuilding them, which
    is dependency order rather than alphabetical.
    """
    result = await service.refresh_analytics(concurrent=concurrent)
    return respond_value(schema.RefreshResult.model_validate(result))


__all__ = ["router"]
