"""Per-request cross-cutting concerns: correlation, cache reporting, timing, limits.

Everything here runs on every request and none of it is analytics. Four jobs:

* a correlation id, generated or adopted, echoed to the client and bound to every log
  line the request produces;
* the ``X-Cache`` header, reporting whether the answer came from cache;
* an access log with the wall-clock duration, since :mod:`app.core.logging` silences
  ``uvicorn.access`` precisely so this one can replace it;
* a per-client request limit.

Plus one thing that is not middleware at all but belongs beside them —
:func:`strict_query`, which rejects query parameters the endpoint never declared.

Pure ASGI, and not by preference
--------------------------------
These are written as ASGI classes taking ``(scope, receive, send)`` rather than as the
more convenient :class:`~starlette.middleware.base.BaseHTTPMiddleware`. That is forced,
and the reason is worth stating because the failure it avoids is invisible.

``BaseHTTPMiddleware`` runs the downstream application in a **separate anyio task**. The
cache tally in :mod:`app.services.base` lives in a :class:`~contextvars.ContextVar`, so a
value recorded while the endpoint runs is written in that child task and is *not* visible
to the middleware afterwards. ``X-Cache`` would then report ``NONE`` on every response
forever — a plausible-looking header rather than an error, so nothing downstream would
ever flag it.

Measured, on this stack: a pure-ASGI middleware reads back ``PARTIAL`` from an endpoint
that recorded one hit and one miss; the same logic in ``BaseHTTPMiddleware`` reads
``NONE``. Worse, one ``BaseHTTPMiddleware`` **anywhere inside the stack** destroys
visibility for correct pure-ASGI middleware wrapping it, because the task boundary is
what matters, not which layer owns it.

So: no ``BaseHTTPMiddleware`` may be added to this application. Starlette's own
``CORSMiddleware``, ``ExceptionMiddleware`` and ``ServerErrorMiddleware`` are all pure
ASGI and are safe.

Errors are rendered here, not raised
------------------------------------
:class:`~app.core.exceptions.RateLimitError` is *rendered* into a response by
:func:`problem_response` rather than raised. Starlette installs app-level exception
handlers in ``ExceptionMiddleware``, which sits **innermost** — beneath user middleware —
so an exception raised out here never reaches a handler. Measured: raising produces a
bare ``500`` with ``text/plain`` and no ``Retry-After``; rendering produces the exact
``429`` problem document, ``Retry-After`` intact.

The body is built by the error's own :meth:`~app.core.exceptions.PrismError.to_problem`,
the same method the handlers in :mod:`app.main` call, so a limit rejection is
byte-identical to the rest of the taxonomy and cannot drift from it.

Ordering
--------
:class:`RequestContextMiddleware` must be **outermost**, with
:class:`RateLimitMiddleware` inside it, so that a rejected request still receives a
correlation id and still appears in the access log. Since ``add_middleware`` makes the
last-added outermost, :mod:`app.main` adds the limiter first and the context second.

Browsers cannot read these headers unless CORS says so
------------------------------------------------------
``X-Request-ID``, ``X-Cache`` and ``X-Response-Time-ms`` are custom response headers, so
cross-origin JavaScript cannot see them unless ``CORSMiddleware`` lists them in
``expose_headers``. :data:`EXPOSED_HEADERS` exists for :mod:`app.main` to pass straight
through, so the two lists cannot fall out of step.
"""

from __future__ import annotations

from http import HTTPStatus
import json
import math
import re
import time
from typing import TYPE_CHECKING, Final

# `Request` is imported at runtime, not under TYPE_CHECKING, because `strict_query` is a
# FastAPI dependency and FastAPI resolves a dependency's annotations at import time. With
# `from __future__ import annotations` the annotation is the string "Request"; if the name
# is absent at runtime FastAPI cannot resolve it to the class and silently reclassifies
# the parameter as a *query parameter* named `request` — so every request to every
# endpoint fails with `{"loc": ["query", "request"], "msg": "Field required"}` before the
# endpoint body runs. Third appearance of this trap: see also `app/services/experiments.py`
# and the `date` import in `app/schemas/params.py`.
from fastapi import Request  # noqa: TC002 — must stay at runtime; see above
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders

from app.core.exceptions import PrismError, RateLimitError, ValidationError
from app.core.logging import (
    REQUEST_ID_KEY,
    bind_request_context,
    clear_request_context,
    get_logger,
    new_request_id,
)
from app.services.base import cache_status, reset_cache_status

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Headers and constants
# ---------------------------------------------------------------------------

#: Correlation id, echoed on every response and accepted on request.
REQUEST_ID_HEADER: Final[str] = "X-Request-ID"

#: Cache outcome: ``HIT``, ``MISS``, ``PARTIAL`` or ``NONE``.
CACHE_HEADER: Final[str] = "X-Cache"

#: Server-side duration in milliseconds.
RESPONSE_TIME_HEADER: Final[str] = "X-Response-Time-ms"

#: Response headers ``CORSMiddleware`` must expose for browser clients to read them.
EXPOSED_HEADERS: Final[tuple[str, ...]] = (
    REQUEST_ID_HEADER,
    CACHE_HEADER,
    RESPONSE_TIME_HEADER,
)

#: Sustained requests allowed per client per minute.
#:
#: Deliberately generous. The dashboard's heaviest page issues roughly a dozen requests
#: on load, and a reader clicking through filters can reasonably produce several bursts
#: a minute. This is a backstop against a runaway loop or a scraper, not a quota.
RATE_LIMIT_PER_MINUTE: Final[int] = 240

#: Requests a client may make instantly before the sustained rate applies.
#:
#: Must comfortably exceed one page load, or opening the dashboard would rate-limit
#: itself — the failure mode that makes a limiter worse than none.
RATE_LIMIT_BURST: Final[int] = 60

#: Ceiling on tracked clients, so the bucket table cannot grow without bound.
MAX_TRACKED_CLIENTS: Final[int] = 8192

#: Bucket key used when the ASGI scope carries no client address.
UNKNOWN_CLIENT: Final[str] = "unknown"

#: Paths exempt from the limit, matched against the end of the path.
#:
#: The container's own health probe runs every ten seconds, and documentation is static.
#: Throttling either would take out monitoring rather than protect anything.
EXEMPT_PATH_SUFFIXES: Final[tuple[str, ...]] = (
    "/health",
    "/openapi.json",
    "/docs",
    "/redoc",
)

#: Shape an inbound correlation id must have to be adopted.
#:
#: An id supplied by a caller is echoed into log lines, so it is untrusted input. Without
#: a pattern check a newline would let a caller forge log records, and an unbounded
#: string would bloat every line the request emits.
_SAFE_REQUEST_ID: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")


# ---------------------------------------------------------------------------
# Rendering errors from outside the handler chain
# ---------------------------------------------------------------------------


def problem_response(
    error: PrismError,
    *,
    path: str,
    request_id: str | None,
) -> tuple[int, bytes, list[tuple[bytes, bytes]]]:
    """Render a :class:`~app.core.exceptions.PrismError` as a raw ASGI response.

    Middleware sits outside ``ExceptionMiddleware``, so a raised error never reaches the
    app-level handlers and degrades to a bare ``500``. This produces the response
    directly instead, from the error's own ``to_problem`` — the same method the handlers
    use, so the body cannot drift from the rest of the taxonomy.

    Args:
        error: The error to render.
        path: Request path, recorded as the problem's ``instance``.
        request_id: Correlation id to echo into the body, if one has been assigned.

    Returns:
        A ``(status_code, body, headers)`` triple ready to hand to ``send``.
    """
    body = json.dumps(
        error.to_problem(instance=path, request_id=request_id),
        separators=(",", ":"),
    ).encode()
    headers = [
        (b"content-type", b"application/problem+json"),
        (b"content-length", str(len(body)).encode()),
    ]
    headers.extend(
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in error.headers.items()
    )
    return error.status_code, body, headers


# ---------------------------------------------------------------------------
# Request context: correlation, cache reporting, timing, access log
# ---------------------------------------------------------------------------


class RequestContextMiddleware:
    """Assign a correlation id, report cache behaviour, time the request, log it.

    Must be the outermost middleware. Everything it does depends on running in the same
    task as the endpoint — see the module docstring for why that is not negotiable.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped application.

        Args:
            app: The next application in the stack.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Wrap one request with context, headers and an access log line.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        method = scope.get("method", "-")
        path = scope.get("path", "-")

        # Both of these are ContextVar writes, and both are read back after the
        # endpoint has run. That only works because this is not BaseHTTPMiddleware.
        reset_cache_status()
        bind_request_context(**{REQUEST_ID_KEY: request_id, "method": method, "path": path})

        # Stashed on the scope so dependencies and handlers can read the id without
        # re-deriving it — `app.main`'s exception handlers echo it into problem bodies.
        scope[REQUEST_ID_KEY] = request_id

        started = time.perf_counter()
        status_seen: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            """Stamp the response headers as the response starts."""
            if message["type"] == "http.response.start":
                status_seen["status"] = message["status"]
                elapsed_ms = (time.perf_counter() - started) * 1000
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                headers[CACHE_HEADER] = cache_status()
                headers[RESPONSE_TIME_HEADER] = f"{elapsed_ms:.1f}"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Log the failure with its correlation id, then let ServerErrorMiddleware
            # turn it into a 500. Swallowing it here would hide the traceback.
            logger.exception(
                "request_failed",
                method=method,
                path=path,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            raise
        else:
            _log_access(
                method=method,
                path=path,
                query=scope.get("query_string", b""),
                status=status_seen.get("status", 0),
                duration_ms=(time.perf_counter() - started) * 1000,
                cache=cache_status(),
            )
        finally:
            # A worker task is recycled across requests, so a context left bound would
            # attach this request's id to the next one's log lines.
            clear_request_context()


def _resolve_request_id(scope: Scope) -> str:
    """Adopt the caller's correlation id when it is safe, else mint a new one.

    Adopting an inbound id lets a trace span a proxy or a client-side retry. The value
    is untrusted and ends up in log lines, so anything not matching
    :data:`_SAFE_REQUEST_ID` is discarded rather than sanitised — a rejected id costs one
    broken trace, while a tolerated newline lets a caller forge log records.

    Args:
        scope: The ASGI connection scope.

    Returns:
        The correlation id for this request.
    """
    wanted = REQUEST_ID_HEADER.lower().encode("latin-1")
    # Annotated locally because `Scope` is a `MutableMapping[str, Any]`, so the raw
    # lookup is untyped and every value read from it would decay to `Any`.
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers", [])
    for key, value in headers:
        if key == wanted:
            candidate = value.decode("latin-1", errors="replace")
            if _SAFE_REQUEST_ID.match(candidate):
                return candidate
            break
    return new_request_id()


def _log_access(
    *,
    method: str,
    path: str,
    query: bytes,
    status: int,
    duration_ms: float,
    cache: str,
) -> None:
    """Emit the access log line for a completed request.

    Replaces ``uvicorn.access``, which :mod:`app.core.logging` silences to make room for
    this. The correlation id is not passed: it is already bound to the logging context,
    so it appears on this line and on every line the endpoint emitted.

    Server errors log at ``error`` and client errors at ``warning``, so a log level of
    ``WARNING`` in production still shows every rejected request.

    Args:
        method: HTTP method.
        path: Request path, without the query string.
        query: Raw query string, recorded so a 422 can be reproduced.
        status: Response status code.
        duration_ms: Wall-clock duration in milliseconds.
        cache: The value reported in ``X-Cache``.
    """
    level = "info"
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        level = "error"
    elif status >= HTTPStatus.BAD_REQUEST:
        level = "warning"

    getattr(logger, level)(
        "request_completed",
        method=method,
        path=path,
        query=query.decode("latin-1", errors="replace") or None,
        status=status,
        duration_ms=round(duration_ms, 1),
        cache=cache,
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimitMiddleware:
    """Throttle each client to a sustained rate with an initial burst.

    A token bucket per client address: :data:`RATE_LIMIT_BURST` tokens of capacity,
    refilling at :data:`RATE_LIMIT_PER_MINUTE` per minute. A bucket that allows a burst
    is the right shape here, because dashboard traffic *is* bursty — a page load fires a
    dozen requests at once and then goes quiet.

    Per worker, not per cluster
    ---------------------------
    State is a dictionary in this process. Under ``--workers N`` each worker keeps its
    own table, so the effective limit is ``N`` times the configured one, and a rolling
    restart forgets every bucket. That is a deliberate trade for a portfolio service
    that must run from a single ``docker compose up`` with no shared store; a
    cluster-wide limit needs Redis, and Redis is optional here by design.

    So this bounds accidental load — a runaway ``useEffect``, a scraper, a load test
    left running. It is not a defence against a distributed attacker, and nothing in the
    stack claims otherwise.

    Concurrency
    -----------
    Read-modify-write on a bucket has no ``await`` inside it, so the event loop cannot
    interleave another request midway and the update needs no lock.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped application and the empty bucket table.

        Args:
            app: The next application in the stack.
        """
        self.app = app
        #: client key -> (tokens remaining, monotonic timestamp of that reading)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._refill_per_second = RATE_LIMIT_PER_MINUTE / 60.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Allow, or reject with a rendered ``429``.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.endswith(EXEMPT_PATH_SUFFIXES):
            await self.app(scope, receive, send)
            return

        allowed, retry_after = self._consume(_client_key(scope))
        if allowed:
            await self.app(scope, receive, send)
            return

        # Rendered, not raised: a raise from out here never reaches the app-level
        # handler and degrades to a bare 500. See the module docstring.
        error = RateLimitError(retry_after_seconds=retry_after)
        status, body, headers = problem_response(
            error,
            path=path,
            request_id=scope.get(REQUEST_ID_KEY),
        )
        logger.warning(
            "rate_limit_rejected",
            method=scope.get("method", "-"),
            path=path,
            retry_after=retry_after,
        )
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def _consume(self, key: str) -> tuple[bool, int]:
        """Take one token from a client's bucket.

        Args:
            key: The client's bucket key.

        Returns:
            ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is meaningful
            only when the request was rejected, and is rounded up so a caller obeying it
            arrives with a whole token rather than a fraction of one.
        """
        now = time.monotonic()
        tokens, last_seen = self._buckets.get(key, (float(RATE_LIMIT_BURST), now))

        # Refill for the elapsed time, capped at the bucket's capacity.
        tokens = min(float(RATE_LIMIT_BURST), tokens + (now - last_seen) * self._refill_per_second)

        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            deficit = 1.0 - tokens
            return False, max(1, math.ceil(deficit / self._refill_per_second))

        self._buckets[key] = (tokens - 1.0, now)
        if len(self._buckets) > MAX_TRACKED_CLIENTS:
            self._evict(now)
        return True, 0

    def _evict(self, now: float) -> None:
        """Drop buckets that have refilled to capacity.

        Bounding the table matters: one entry per distinct address is a slow memory leak
        under scanner traffic, which arrives from many addresses exactly once each.

        Eviction here is free rather than approximate. A bucket at full capacity is
        indistinguishable from one that has never been seen — both start full — so
        deleting it cannot let a client through that the limit would otherwise have
        stopped. Only idle clients are forgotten; a client actively being throttled has a
        depleted bucket and is therefore never a candidate.

        Args:
            now: The current monotonic timestamp.
        """
        capacity = float(RATE_LIMIT_BURST)
        self._buckets = {
            key: (tokens, last_seen)
            for key, (tokens, last_seen) in self._buckets.items()
            if min(capacity, tokens + (now - last_seen) * self._refill_per_second) < capacity
        }


def _client_key(scope: Scope) -> str:
    """Return the bucket key for the caller.

    Reads ``scope["client"]``, which uvicorn populates from the peer address, or from
    ``X-Forwarded-For`` when started with ``--proxy-headers`` and a trusted
    ``--forwarded-allow-ips`` — as the compose service is. Reading the header directly
    here instead would be wrong: it is caller-supplied, so anyone could rotate the value
    and get a fresh bucket per request, which is a limiter that does nothing.

    Args:
        scope: The ASGI connection scope.

    Returns:
        The client's address, or :data:`UNKNOWN_CLIENT` when the transport reports none.
    """
    client = scope.get("client")
    if client:
        return str(client[0])
    return UNKNOWN_CLIENT


# ---------------------------------------------------------------------------
# Rejecting undeclared query parameters
# ---------------------------------------------------------------------------


def declared_query_names(route: APIRoute) -> set[str]:
    """Collect every query parameter name a route accepts, dependencies included.

    Walks the whole dependency tree rather than the endpoint signature, because
    :mod:`app.schemas.params` supplies parameters through ``Depends`` callables and their
    names appear only on the nested dependants. A shallow read would reject
    ``?country=`` — a valid parameter — which is worse than the problem being solved.

    Args:
        route: The matched route.

    Returns:
        Declared query parameter names, by alias where one is set.
    """
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        names.update(field.alias or field.name for field in dependant.query_params)
        stack.extend(dependant.dependencies)
    return names


def strict_query(request: Request) -> None:
    """Reject query parameters the matched endpoint does not declare.

    Why this exists
    ---------------
    Ignoring an unknown parameter is the worst available outcome. ``?contry=India`` is a
    typo and ``?countries=India`` is a plausible guess at the spelling; both would
    otherwise return ``200`` with the *whole* dataset while the caller believes it was
    filtered. A wrong number that looks right is harder to catch than an error, and it
    is the reason the fields in :mod:`app.schemas.params` are named without aliases.

    Why it is a dependency and not middleware
    -----------------------------------------
    The check needs the matched route, and ``scope["route"]`` is ``None`` in middleware
    before ``call_next`` — routing happens further in. It is only populated afterwards,
    by which point the query has already run and the rejection would be too late to save
    any work. In a dependency the route is already there. Measured, not assumed.

    A ``PrismError`` raised in a dependency does reach the app-level handlers, so this
    yields the taxonomy's ``422`` rather than a ``500``.

    Args:
        request: The inbound request.

    Raises:
        ValidationError: When any query parameter was not declared.
    """
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        # No matched route means nothing to compare against. Reachable in principle for
        # a non-APIRoute mount; skipping is correct, since rejecting on an unknown
        # contract would be a guess.
        return

    declared = declared_query_names(route)
    unknown = sorted(set(request.query_params) - declared)
    if not unknown:
        return

    accepted = ", ".join(sorted(declared)) or "no query parameters"
    raise ValidationError(
        f"Unknown query parameter(s): {', '.join(unknown)}.",
        errors=[
            {
                "field": name,
                "message": "Not a parameter of this endpoint.",
            }
            for name in unknown
        ]
        + [
            {
                "field": "_accepted",
                "message": f"This endpoint accepts: {accepted}.",
            }
        ],
    )


__all__ = [
    "CACHE_HEADER",
    "EXPOSED_HEADERS",
    "RATE_LIMIT_BURST",
    "RATE_LIMIT_PER_MINUTE",
    "REQUEST_ID_HEADER",
    "RESPONSE_TIME_HEADER",
    "RateLimitMiddleware",
    "RequestContextMiddleware",
    "declared_query_names",
    "problem_response",
    "strict_query",
]
