"""The ASGI application: lifespan, error handling, middleware, routes.

This module is assembly only. It owns no analytics logic and no SQL — it decides what
happens before the first request and after the last, how an error becomes a response, and
which routers are mounted where.

Startup is strict about the engine and lenient about everything else
-------------------------------------------------------------------
:func:`~app.db.session.init_engine` is allowed to fail the boot. A database that cannot be
reached at all is a misconfiguration, and surfacing it at startup puts the error where an
operator is already looking instead of on a caller's first request.

:func:`~app.db.deps.init_dimension_catalog` deliberately does *not* fail the boot. A
reachable-but-unmigrated database is a different situation with a different fix, and the
service is more useful up: ``/health`` can report ``degraded`` and name the fix, and filter
validation rejects values with a clear message rather than the process refusing to start.
:func:`~app.core.security.check_admin_key_strength` warns for the same reason — a default
key must not stop ``docker compose up`` from working.

Middleware order is load-bearing, in two independent ways
---------------------------------------------------------
``add_middleware`` makes the **last added outermost**, so the calls below read
inside-out. Two constraints fix the arrangement:

* :class:`~app.middleware.RequestContextMiddleware` must wrap
  :class:`~app.middleware.RateLimitMiddleware`, so a throttled request still gets a
  correlation id and still appears in the access log. A rejection that leaves no trace is
  the one you most want a trace of.
* **No** ``BaseHTTPMiddleware`` may be added here. It runs the inner app in a separate
  task, which severs the ``ContextVar`` the cache tally uses — and it does so for correct
  pure-ASGI middleware wrapping it too, because the task boundary is what matters rather
  than which layer owns it. The symptom is not an error: ``X-Cache`` quietly reads ``NONE``
  on every response forever. Starlette's ``CORSMiddleware``, ``ExceptionMiddleware`` and
  ``ServerErrorMiddleware`` are all pure ASGI and safe.

This exact arrangement is the one verified end to end, CORS included, rather than a
rearrangement that looks equivalent.

Every error is one document shape
---------------------------------
Three handlers, all rendering RFC 7807 ``application/problem+json``:

* :class:`~app.core.exceptions.PrismError` covers the whole taxonomy through its base
  class, so a new subclass is handled the day it is written.
* ``RequestValidationError`` is FastAPI's own, raised when a parameter fails a declared
  constraint such as ``limit=0``. Left alone it returns FastAPI's default envelope, so a
  bad value and a bad *name* would answer in two different shapes from the same endpoint.
* ``Exception`` is the backstop. It logs with the traceback and returns a fixed message,
  because an unexpected error's text can carry a query fragment or a connection string.

Rate-limit rejections do not pass through here — middleware sits outside
``ExceptionMiddleware``, so :mod:`app.middleware` renders them itself using the same
``to_problem`` method these handlers call.
"""

# No `from __future__ import annotations` in this module. FastAPI resolves the annotations
# on the handlers and the lifespan at import time; postponed evaluation turns them into
# strings it cannot resolve. Fifth appearance of this trap — see `app/routers/kpi.py`.
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from app.core.cache import close_cache, init_cache
from app.core.config import get_settings
from app.core.exceptions import PrismError, ValidationError
from app.core.logging import REQUEST_ID_KEY, configure_logging, get_logger
from app.core.security import check_admin_key_strength
from app.db.deps import init_dimension_catalog
from app.db.session import dispose_engine, init_engine
from app.middleware import (
    EXPOSED_HEADERS,
    RateLimitMiddleware,
    RequestContextMiddleware,
    strict_query,
)
from app.routers import ALL_ROUTERS

logger = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"

#: Shown above the tag list in ``/docs``.
DESCRIPTION = """
Product analytics for **Vireo**, a fictional streaming service.

Every endpoint returns `{"data": ..., "meta": ...}`. `meta` carries the row count, the
window that was actually queried, whether filters applied, and the correlation id — the
same value as the `X-Request-ID` response header.

**Windows are required and have no defaults.** A "last 30 days" default would open every
chart empty on a dataset generated months earlier. Call `/meta/bounds` for the real first
and last activity dates, and `/meta/filters` for every filter's accepted values.

**Nulls are meaningful.** A `null` is an undefined or not-yet-observable figure — a ratio
with an empty denominator, a cohort cell whose window has not elapsed — never a missing
number to be read as zero.

Errors are [RFC 7807](https://www.rfc-editor.org/rfc/rfc7807) problem documents.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare process-wide resources, then release them.

    Args:
        app: The application being started. Unused: everything here is process-wide
            rather than attached to the instance, which keeps the resources reachable
            from scripts and tests that never build an app.

    Yields:
        Once, while the application serves requests.
    """
    configure_logging()
    settings = get_settings()

    logger.info(
        "api_starting",
        environment=str(settings.env),
        version=settings.api.version,
        docs_exposed=settings.expose_docs,
    )

    # Raises on failure, by design: an unreachable database is a misconfiguration, and
    # boot is where it should be visible.
    await init_engine()

    # Does not raise: an unmigrated database should still serve /health and say so.
    await init_dimension_catalog()

    # Must happen here. `get_cache()` falls back to a no-op `DisabledCache` when no
    # backend has been installed, so skipping this does not fail — it silently disables
    # caching process-wide and every request recomputes. Nothing errors, nothing logs, and
    # the only visible trace is `X-Cache` reading MISS forever.
    cache = await init_cache()

    check_admin_key_strength()

    logger.info(
        "api_ready",
        prefix=settings.api.prefix,
        routers=len(ALL_ROUTERS),
        # Which backend actually installed, not which was configured: Redis is optional
        # and falls back to the local LRU on any failure, so `local` here after asking for
        # Redis means Redis is unreachable.
        cache_backend=cache.name,
    )

    try:
        yield
    finally:
        # In a finally block so a crash during serving still returns pooled connections
        # and closes the Redis client.
        await close_cache()
        await dispose_engine()
        logger.info("api_stopped")


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than module-level statements, so tests can construct an isolated
    instance and ``--reload`` has a stable target. The module-level :data:`app` below is
    what ``uvicorn app.main:app`` serves.

    Returns:
        The configured application.
    """
    settings = get_settings()

    application = FastAPI(
        title=settings.api.title,
        version=settings.api.version,
        description=DESCRIPTION,
        lifespan=lifespan,
        root_path=settings.api.root_path,
        # Gated together: a hidden /docs that still serves /openapi.json is not hidden,
        # since any client can render the schema itself.
        docs_url="/docs" if settings.expose_docs else None,
        redoc_url="/redoc" if settings.expose_docs else None,
        openapi_url="/openapi.json" if settings.expose_docs else None,
        # Applied to every route in the app. Unknown query parameters are rejected here
        # rather than per-router, because the failure being prevented — `?contry=India`
        # returning an unfiltered 200 that looks filtered — is worst on the endpoint
        # somebody forgot to opt in.
        dependencies=[Depends(strict_query)],
    )

    _register_error_handlers(application)
    _register_middleware(application, settings.api.cors_origin_list)

    for router in ALL_ROUTERS:
        application.include_router(router, prefix=settings.api.prefix)

    return application


def _register_error_handlers(application: FastAPI) -> None:
    """Attach the three problem-document handlers.

    Args:
        application: The application to attach to.
    """

    def _problem(request: Request, error: PrismError) -> JSONResponse:
        """Render any taxonomy error as a problem document."""
        return JSONResponse(
            status_code=error.status_code,
            content=error.to_problem(
                instance=str(request.url.path),
                request_id=request.scope.get(REQUEST_ID_KEY),
            ),
            headers=error.headers,
            media_type=PROBLEM_JSON,
        )

    @application.exception_handler(PrismError)
    async def handle_prism_error(request: Request, exc: Exception) -> JSONResponse:
        """Render the whole taxonomy, including subclasses added later.

        The signature is typed ``Exception`` because that is what Starlette's handler
        protocol declares; the registration key guarantees what actually arrives.
        """
        assert isinstance(exc, PrismError)  # narrowing; guaranteed by the registration key
        return _problem(request, exc)

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(request: Request, exc: Exception) -> JSONResponse:
        """Convert FastAPI's own parameter failures into the taxonomy's shape.

        So ``?limit=0`` and ``?limt=5`` answer in the same document shape from the same
        endpoint. Each of FastAPI's error entries becomes one ``field``/``message`` pair,
        with the parameter name taken from the tail of its ``loc``.
        """
        assert isinstance(exc, RequestValidationError)  # guaranteed by the registration key
        errors = [
            {
                "field": str(entry["loc"][-1]) if entry.get("loc") else "request",
                "message": str(entry.get("msg", "Invalid value.")),
            }
            for entry in exc.errors()
        ]
        return _problem(
            request,
            ValidationError("One or more parameters are invalid.", errors=errors),
        )

    @application.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Log an unhandled exception and return a fixed 500 document.

        The message is fixed rather than derived from the exception: an unexpected error's
        text can carry a SQL fragment or a connection string, and a caller cannot act on
        either. The correlation id is what ties the response to the logged traceback.
        """
        request_id = request.scope.get(REQUEST_ID_KEY)
        logger.exception(
            "unhandled_exception",
            path=str(request.url.path),
            error_type=type(exc).__name__,
            **{REQUEST_ID_KEY: request_id},
        )
        return JSONResponse(
            status_code=500,
            content=PrismError("An unexpected error occurred.").to_problem(
                instance=str(request.url.path),
                request_id=request_id,
            ),
            media_type=PROBLEM_JSON,
        )


def _register_middleware(application: FastAPI, cors_origins: list[str]) -> None:
    """Add the middleware stack, innermost first.

    Args:
        application: The application to add to.
        cors_origins: Allowed browser origins.
    """
    # Innermost of the three. Inside the context middleware, so a throttled request is
    # still assigned a correlation id and still logged.
    application.add_middleware(RateLimitMiddleware)

    # Correlation id, X-Cache, timing, access log.
    application.add_middleware(RequestContextMiddleware)

    # Outermost, so a preflight is answered without waking anything further in.
    # `expose_headers` is required: without it a browser can receive the custom headers
    # but JavaScript cannot read them, which looks exactly like them not being sent.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["X-API-Key", "X-Request-ID", "Content-Type"],
        expose_headers=list(EXPOSED_HEADERS),
    )


#: The ASGI application. Referenced by `uvicorn app.main:app` in the Dockerfile and in
#: docker-compose.yml.
app = create_app()

__all__ = ["app", "create_app", "lifespan"]
