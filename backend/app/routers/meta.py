"""The service's own metadata: filter catalogue, dataset bounds, health.

Three endpoints that are not analytics. They exist so a client can discover what to ask
for before asking, and so an orchestrator can tell whether asking is worth it yet.

No prefix on this router, deliberately
--------------------------------------
``/health`` must resolve at ``{api.prefix}/health`` rather than under a ``/meta`` segment.
Two things depend on that exact path: the container healthcheck in ``docker-compose.yml``
polls it every ten seconds, and :data:`app.middleware.EXEMPT_PATH_SUFFIXES` exempts the
``/health`` suffix from rate limiting — a probe that gets itself throttled would report a
healthy service as failing. So paths are written out in full here instead of being composed
from a router prefix.

Why the window parameters have no defaults, and what replaces them
------------------------------------------------------------------
``/meta/bounds`` is the answer to a problem it would be easy to solve wrongly. A "last 30
days" default on every window would open every chart empty for anyone running this
repository months after the data was generated. Rather than guess, the API requires an
explicit window and publishes the real first and last activity dates here, so a client can
pick one that contains data.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from fastapi import APIRouter, Response, status

from app.db.deps import CatalogDep, SessionDep
from app.routers.base import respond_value
from app.schemas import meta as schema
from app.schemas.base import ValueResponse, with_rate_limit
from app.services import meta as service

router = APIRouter(tags=["Meta"], responses=with_rate_limit())


@router.get(
    "/meta/filters",
    response_model=ValueResponse[schema.FilterOptions],
    summary="Valid values for every filter",
)
async def get_filter_options(catalog: CatalogDep) -> ValueResponse[schema.FilterOptions]:
    """Return every filter's accepted values.

    Read from the dimension catalogue loaded at startup, not hard-coded, so a dimension
    row added by a migration appears in the dashboard's filter bar without a frontend
    change.

    Countries are listed by name only. ISO codes remain accepted as *input* — the catalogue
    resolves either spelling — but listing both would double the length of a list a person
    reads.
    """
    options = service.get_filter_options(catalog)
    return respond_value(schema.FilterOptions.model_validate(options))


@router.get(
    "/meta/bounds",
    response_model=ValueResponse[schema.DatasetBounds],
    summary="First and last activity date in the dataset",
)
async def get_dataset_bounds(session: SessionDep) -> ValueResponse[schema.DatasetBounds]:
    """Return the span and size of the seeded dataset.

    ``events`` is approximate — read from the query planner's row estimate rather than
    counted, because an exact count over a 65-partition table is a full scan and this
    figure exists for orientation rather than arithmetic. The field says so in its own
    description, so a reader is not left to discover it from a number that nearly matches.

    On a migrated but unseeded database ``is_seeded`` is ``False`` and the dates are
    ``None``. That is not an error: the analytics endpoints will return empty series rather
    than failing, and this is how a client tells the two situations apart.
    """
    bounds = await service.get_dataset_bounds(session)
    return respond_value(schema.DatasetBounds.model_validate(bounds))


@router.get(
    "/health",
    response_model=schema.HealthStatus,
    summary="Liveness and readiness",
)
async def get_health(response: Response) -> schema.HealthStatus:
    """Report whether the service and its database are ready.

    Returned bare rather than in the standard envelope. This is an operational probe
    consumed by orchestrators, not analytics data, and wrapping a healthcheck in a
    ``meta``/``data`` envelope would make the one field a probe reads harder to reach.

    Three states, because the middle one is the only one with an actionable fix:

    * ``ok`` — connected, migrated, and the analytics views hold data.
    * ``degraded`` — reachable but not ready, typically migrated and never seeded. Served
      as **200**: the process is up and answering, and the fix is ``make seed`` rather than
      a restart. Returning 503 here would make a container that is working correctly look
      broken for as long as the database sits empty.
    * ``error`` — the database is not reachable. Served as **503**, so the container
      healthcheck fails and an orchestrator can act on it.

    ``cache_backend`` reports which backend is actually serving, not which was configured.
    Redis is optional and the in-process LRU is a deliberate fallback, so seeing ``local``
    when Redis was requested means Redis is unreachable — worth knowing, and invisible if
    this only echoed the setting.
    """
    health = await service.get_health()
    if health.get("status") == service.STATUS_ERROR:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return schema.HealthStatus.model_validate(health)


__all__ = ["router"]
