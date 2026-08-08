"""Response envelopes, shared field types, and the problem-details model.

Every response this API returns has one of two shapes: a :class:`DataResponse`
carrying rows and a :class:`ResponseMeta`, or an RFC 7807 problem document. Both are
declared here so the contract exists in one place and OpenAPI can name it.

Why an envelope rather than a bare array
----------------------------------------
A bare JSON array leaves nowhere to put the things a reader of the number needs: how
the window was interpreted after validation, whether filters narrowed the population,
whether the figures came from cache. Those could live only in headers — and ``X-Cache``
and ``X-Request-ID`` are sent as headers too — but a chart that saves a response to a
file, or a notebook that reads one, keeps the body and loses the headers. The window
echo matters most: a caller who asked for a 900-day window and received a 422 knows
what happened, but a caller whose dates were accepted has no other confirmation of
which days the numbers describe.

``meta`` is therefore duplication with the headers, deliberately. The header serves the
browser; the body serves whatever keeps the payload.

Decimals cross the wire as numbers
----------------------------------
The repository and service layers work hard to keep money and rates as
:class:`~decimal.Decimal`, and Pydantic's default JSON encoding renders a ``Decimal``
as a *string* to preserve that exactness. This module overrides that: :data:`Number`
serialises to a JSON number.

The reasoning, since it trades one correctness property for another. Every decimal this
API returns has already been rounded by SQL to two or three decimal places, so the
values are small, bounded, and exactly representable as doubles — the precision a
string would protect has already been spent upstream. Against that, a string costs
every consumer a parse: Recharts plots ``"12.34"`` as a category rather than a
magnitude, and silently, with an axis that looks plausible. The exactness matters where
arithmetic happens, which is in Postgres and in :mod:`app.services.stats`, and both are
upstream of here.

What survives is the type *distinction*: an ``int`` column stays an integer and a
``Decimal`` column stays a fractional number, which is what a schema is for. What is
given up is the last-digit guarantee on a value that no longer has digits to lose.

Row models tolerate unknown columns
-----------------------------------
:class:`RowModel` sets ``extra="ignore"``, so a column added to a ``.sql`` file does not
break the endpoint that reads it. That looks like the loose choice and is the opposite:
drift between a model and its query is caught by the phase 12 gate, which asserts the
declared fields match the returned keys exactly, rather than by a 500 in front of a
reader. Strictness belongs in the test, not in the request path.

Request-side models — query parameters in :mod:`app.schemas.params` — use
``extra="forbid"`` instead, because there a misspelled parameter is a caller mistake
worth naming rather than silently dropping.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from app.core.exceptions import PROBLEM_BASE_URI

# ---------------------------------------------------------------------------
# Shared field types
# ---------------------------------------------------------------------------


def _to_seconds(value: Any) -> Any:
    """Convert a :class:`~datetime.timedelta` to a count of seconds.

    Args:
        value: A field value on its way into a model.

    Returns:
        ``Decimal`` seconds for a ``timedelta``; anything else unchanged, so normal
        numeric validation still applies and reports its own errors.
    """
    if isinstance(value, timedelta):
        # str() first: Decimal(float) would introduce binary error in a value the
        # rest of this layer treats as exact.
        return Decimal(str(value.total_seconds()))
    return value


#: A fractional value: money, a rate, a percentage, an average. Carried as a
#: :class:`~decimal.Decimal` in Python and serialised as a JSON number — see the
#: module docstring for why the default string encoding is overridden.
#:
#: ``json-unless-none`` rather than ``json``: an optional field must stay ``null``,
#: and ``float(None)`` would raise.
Number = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json-unless-none"),
]

#: A duration in seconds. Distinct from :data:`Number` only in what it accepts: a
#: :class:`~datetime.timedelta` is converted rather than rejected.
#:
#: Every duration in the 48 queries is already numeric by the time it leaves Postgres
#: — each one goes through ``EXTRACT(EPOCH FROM ...)`` and ``ROUND(...::numeric)``, so
#: it arrives as a :class:`~decimal.Decimal`. The ``timedelta`` branch is therefore
#: unreached today, and it is here because the layer below can produce one: the cache
#: codec in :mod:`app.services.base` carries a ``td`` tag that restores a real
#: ``timedelta`` on read. A schema type that rejected what the codec is built to
#: return would be a 500 waiting for the first ``interval`` column, and the two layers
#: are better off agreeing.
#:
#: Without the converter a bare ``timedelta`` fails validation outright — Pydantic
#: does not coerce one to ``Decimal``. Verified, not assumed.
Seconds = Annotated[
    Decimal,
    BeforeValidator(_to_seconds),
    PlainSerializer(float, return_type=float, when_used="json-unless-none"),
]


# ---------------------------------------------------------------------------
# Base models
# ---------------------------------------------------------------------------


class PrismModel(BaseModel):
    """Base for every model this API declares.

    Frozen, because a response model is a description of something already computed
    and mutating one after the fact would mean the body and the log line disagree.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        ser_json_timedelta="float",
    )


class RowModel(PrismModel):
    """Base for models built from a query row.

    Differs from :class:`PrismModel` in one respect: unknown columns are ignored
    rather than rejected. See the module docstring — the mismatch is worth failing on
    in a test, not in front of a reader.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
        ser_json_timedelta="float",
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class WindowEcho(PrismModel):
    """The reporting window as the API interpreted it.

    Echoed so a caller can confirm which days the numbers describe. Both ends are
    inclusive, which is the one thing about a date range that cannot be inferred from
    the range itself.

    Attributes:
        date_from: First day included.
        date_to: Last day included.
        days: Length in days, counting both endpoints.
    """

    date_from: date
    date_to: date
    days: int = Field(ge=1)


class ResponseMeta(PrismModel):
    """Context for one response: provenance, cache behaviour and shape.

    Attributes:
        cache: ``HIT`` when every cache lookup this request made was served from
            cache, ``MISS`` when none were, ``PARTIAL`` when a composite endpoint
            mixed both, and ``NONE`` when the endpoint does not cache — which is a
            different statement from having cached and missed.
        rows: Number of rows in ``data``. Redundant against the array's length, and
            worth carrying anyway: a consumer that streams or truncates the payload
            can still tell whether it saw all of it.
        window: The validated window, when the endpoint takes one.
        filters_applied: Whether any filter narrowed the population. ``True`` means
            the figures describe a segment, not the whole user base.
        generated_at: When the response was assembled, UTC. Not when the underlying
            data was computed — a cache hit reports the time of this request, and
            ``cache`` is what distinguishes the two.
        request_id: Correlation id, also sent as ``X-Request-ID``. Quote it in a bug
            report and the matching log lines can be found.
    """

    cache: str = "NONE"
    rows: int = Field(default=0, ge=0)
    window: WindowEcho | None = None
    filters_applied: bool = False
    generated_at: datetime
    request_id: str | None = None


class DataResponse[RowT](PrismModel):
    """A list of rows with its metadata.

    The shape every analytics endpoint returns. Generic over the row model, so
    OpenAPI names each endpoint's rows concretely rather than describing an object.

    Attributes:
        data: The rows, in the order the query returned them. Order is meaningful for
            every series and leaderboard in this API and is never re-sorted here.
        meta: Provenance and cache context.
    """

    data: list[RowT]
    meta: ResponseMeta


class ValueResponse[ValueT](PrismModel):
    """A single computed object with its metadata.

    For the endpoints that return one thing rather than rows — the overview tiles, an
    experiment's results, the filter catalogue. Sharing the envelope means a consumer
    reads ``meta`` the same way everywhere.

    Attributes:
        data: The computed value.
        meta: Provenance and cache context.
    """

    data: ValueT
    meta: ResponseMeta


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProblemFieldError(PrismModel):
    """One field-level detail inside a validation problem.

    Attributes:
        field: The parameter at fault.
        message: What is wrong with it.
    """

    field: str
    message: str


class ProblemDetail(PrismModel):
    """An RFC 7807 problem document.

    Mirrors :meth:`app.core.exceptions.PrismError.to_problem` field for field. It
    exists so OpenAPI can describe the error shape; the handlers in
    :mod:`app.main` render from the exception, never from this model. Two
    descriptions of one shape can disagree, so the direction of truth is stated
    plainly: :mod:`app.core.exceptions` is authoritative and this is documentation of
    it, checked by a test rather than by construction.

    Attributes:
        type: Stable problem-type URI. Clients switch on this rather than parsing
            ``detail``.
        title: Short summary, stable for a given error kind.
        status: HTTP status code, repeated in the body so a logged payload is
            self-contained.
        detail: What went wrong on this specific request.
        instance: The request path.
        errors: Per-field breakdown, present on validation failures.
        request_id: Correlation id for the failing request.
    """

    type: str = Field(examples=[f"{PROBLEM_BASE_URI}/validation-error"])
    title: str = Field(examples=["Validation error"])
    status: int = Field(ge=400, le=599, examples=[422])
    detail: str = Field(examples=["date_from must not be later than date_to"])
    instance: str = Field(examples=["/api/v1/kpis/dau"])
    errors: list[ProblemFieldError] | None = None
    request_id: str | None = None


#: OpenAPI ``responses`` entries every analytics route shares. Declared once: a route
#: that documents only its 200 tells a client generator that nothing else can happen,
#: which is false for all of them.
#:
#: 401 and 429 are absent deliberately — the first applies only to the admin route,
#: and the second is added by :func:`with_rate_limit` where a limiter is in force.
COMMON_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    422: {"model": ProblemDetail, "description": "Invalid parameters or unknown filter value"},
    500: {"model": ProblemDetail, "description": "Unexpected server error"},
    503: {
        "model": ProblemDetail,
        "description": "Database unavailable, or analytics views not populated",
    },
    504: {"model": ProblemDetail, "description": "Query exceeded the statement timeout"},
}


def with_rate_limit(
    responses: dict[int | str, dict[str, Any]] | None = None,
) -> dict[int | str, dict[str, Any]]:
    """Return route responses including the 429 the limiter can raise.

    Args:
        responses: Extra route-specific entries, merged over the common set.

    Returns:
        A new mapping; the module-level constant is never mutated.
    """
    merged: dict[int | str, dict[str, Any]] = {
        **COMMON_ERROR_RESPONSES,
        429: {"model": ProblemDetail, "description": "Rate limit exceeded"},
    }
    if responses:
        merged.update(responses)
    return merged


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def build_meta(
    *,
    rows: int,
    window: WindowEcho | None = None,
    filters_applied: bool = False,
) -> ResponseMeta:
    """Assemble the metadata for one response.

    Reads the per-request cache tally from :mod:`app.services.base` and the
    correlation id from the structlog context, so a router does not thread either
    through its signature.

    Importing a service from a schema module looks backwards and is not: services
    import nothing from schemas, so there is no cycle, and
    :func:`~app.services.base.cache_status` is pure telemetry with no ``fastapi``
    dependency of its own.

    Args:
        rows: Row count for the payload.
        window: The validated window, when the endpoint took one.
        filters_applied: Whether filters narrowed the population.

    Returns:
        The metadata block.
    """
    # Imported here rather than at module scope: `structlog.contextvars` is read at
    # call time, and keeping the dependency local makes it obvious this function is
    # request-scoped while everything else in the module is a pure declaration.
    import structlog

    from app.core.logging import REQUEST_ID_KEY
    from app.services.base import cache_status

    context = structlog.contextvars.get_contextvars()
    request_id = context.get(REQUEST_ID_KEY)

    return ResponseMeta(
        cache=cache_status(),
        rows=rows,
        window=window,
        filters_applied=filters_applied,
        generated_at=datetime.now(UTC),
        request_id=str(request_id) if request_id else None,
    )


__all__ = [
    "COMMON_ERROR_RESPONSES",
    "DataResponse",
    "Number",
    "PrismModel",
    "ProblemDetail",
    "ProblemFieldError",
    "ResponseMeta",
    "RowModel",
    "Seconds",
    "ValueResponse",
    "WindowEcho",
    "build_meta",
    "with_rate_limit",
]
