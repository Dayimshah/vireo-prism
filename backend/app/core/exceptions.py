"""Domain exceptions and RFC 7807 problem-details rendering.

Every error the API returns has the same shape, defined once here. Handlers are
registered in ``app/main.py``; services and repositories raise these types and
never build a response themselves.

The response body follows `RFC 7807 <https://www.rfc-editor.org/rfc/rfc7807>`_::

    {
      "type":     "https://prism.vireo.dev/problems/validation-error",
      "title":    "Validation error",
      "status":   422,
      "detail":   "date_from must not be later than date_to",
      "instance": "/api/v1/kpis",
      "errors":   [{"field": "date_from", "message": "..."}],
      "request_id": "8f14e45fceea167a5a36dedd4bea2543"
    }

Why a taxonomy rather than raising ``HTTPException`` inline: the service layer
should not import ``fastapi``. Keeping HTTP concerns at the edge means services
stay unit-testable without a request context, and the same service can be
called from the seeder or a script.
"""

from __future__ import annotations

from typing import Any, Final

#: Base URI for problem type identifiers. Not dereferenced at runtime; it exists
#: so each error kind has a stable, greppable identity that clients can switch on
#: instead of parsing prose.
PROBLEM_BASE_URI: Final[str] = "https://prism.vireo.dev/problems"


class PrismError(Exception):
    """Base class for every deliberate application error.

    Attributes:
        status_code: HTTP status the edge should return.
        problem_type: Slug appended to :data:`PROBLEM_BASE_URI`.
        title: Short, human-readable summary. Stable per error class.
        detail: Instance-specific explanation, safe to show a caller.
        errors: Optional per-field breakdown for validation failures.
        headers: Extra response headers, e.g. ``Retry-After``.
    """

    status_code: int = 500
    problem_type: str = "internal-error"
    title: str = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            detail: Instance-specific message. Falls back to :attr:`title`.
            errors: Field-level detail for validation problems.
            headers: Additional response headers.
        """
        self.detail = detail or self.title
        self.errors = errors or []
        self.headers = headers or {}
        super().__init__(self.detail)

    def to_problem(self, *, instance: str, request_id: str | None = None) -> dict[str, Any]:
        """Render this error as an RFC 7807 problem-details document.

        Args:
            instance: Request path that produced the error.
            request_id: Correlation id, echoed so a user can quote it in a bug
                report and the matching log line can be found.

        Returns:
            A JSON-serialisable problem-details mapping.
        """
        problem: dict[str, Any] = {
            "type": f"{PROBLEM_BASE_URI}/{self.problem_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": instance,
        }
        if self.errors:
            problem["errors"] = self.errors
        if request_id:
            problem["request_id"] = request_id
        return problem


# ---------------------------------------------------------------------------
# 4xx — the caller can fix these
# ---------------------------------------------------------------------------


class ValidationError(PrismError):
    """A request parameter was structurally valid but semantically wrong.

    Raised for cross-field problems Pydantic cannot express on its own: a date
    range that runs backwards, a window wider than the configured maximum, a
    funnel step list containing duplicates.
    """

    status_code = 422
    problem_type = "validation-error"
    title = "Validation error"


class UnknownDimensionValueError(ValidationError):
    """A filter value is not present in its dimension table.

    Every filter value is checked against ``core.countries``, ``core.devices``
    and friends before it reaches SQL. This is the primary defence against
    injection through filter parameters, and it also turns a typo into a clear
    422 instead of a silently empty chart — which is the failure mode that
    actually wastes an analyst's afternoon.
    """

    problem_type = "unknown-dimension-value"
    title = "Unknown filter value"

    def __init__(self, dimension: str, values: list[str], *, allowed: list[str] | None = None) -> None:
        """Initialise the error.

        Args:
            dimension: Filter name, e.g. ``"country"``.
            values: The rejected values.
            allowed: Valid values, included when the set is small enough to be
                useful rather than overwhelming.
        """
        rejected = ", ".join(repr(value) for value in values)
        detail = f"Unknown {dimension} value(s): {rejected}."
        if allowed and len(allowed) <= 30:
            detail += f" Valid values: {', '.join(sorted(allowed))}."
        super().__init__(
            detail,
            errors=[{"field": dimension, "message": f"unknown value: {value}"} for value in values],
        )


class NotFoundError(PrismError):
    """A requested entity does not exist."""

    status_code = 404
    problem_type = "not-found"
    title = "Resource not found"

    def __init__(self, resource: str, identifier: str | int) -> None:
        """Initialise the error.

        Args:
            resource: Entity kind, e.g. ``"user"``.
            identifier: The identifier that was looked up.
        """
        super().__init__(f"No {resource} found with identifier {identifier!r}.")


class UnauthorizedError(PrismError):
    """A privileged endpoint was called without a valid credential.

    Only reachable via ``POST /api/v1/admin/refresh``. Read endpoints are
    deliberately open — see ``docs/decisions.md``.
    """

    status_code = 401
    problem_type = "unauthorized"
    title = "Unauthorized"

    def __init__(self, detail: str = "A valid X-API-Key header is required.") -> None:
        """Initialise the error.

        Args:
            detail: Message shown to the caller.
        """
        # WWW-Authenticate is required on a 401 by RFC 9110. ApiKey is not a
        # registered scheme, but naming the header is more useful to a client
        # than omitting the field.
        super().__init__(detail, headers={"WWW-Authenticate": 'ApiKey realm="prism"'})


class RateLimitError(PrismError):
    """Too many requests from one caller."""

    status_code = 429
    problem_type = "rate-limit-exceeded"
    title = "Rate limit exceeded"

    def __init__(self, retry_after_seconds: int = 60) -> None:
        """Initialise the error.

        Args:
            retry_after_seconds: Seconds the caller should wait.
        """
        super().__init__(
            f"Rate limit exceeded. Retry in {retry_after_seconds} seconds.",
            headers={"Retry-After": str(retry_after_seconds)},
        )


# ---------------------------------------------------------------------------
# 5xx — the caller cannot fix these
# ---------------------------------------------------------------------------


class QueryNotFoundError(PrismError):
    """A repository asked the SQL registry for a name that is not loaded.

    A programming error, not a runtime condition: the registry loads every
    ``.sql`` file at startup, so this can only fire on a typo in a repository or
    a query file that was renamed without updating its caller. It surfaces as a
    500 rather than a 404 because the caller did nothing wrong.
    """

    status_code = 500
    problem_type = "query-not-found"
    title = "Analytics query not found"

    def __init__(self, name: str, *, available: int = 0) -> None:
        """Initialise the error.

        Args:
            name: The requested query name.
            available: How many queries the registry did load, which
                distinguishes "typo" from "registry never loaded".
        """
        super().__init__(
            f"No SQL query registered under {name!r} ({available} queries loaded)."
        )


class DatabaseError(PrismError):
    """A database operation failed.

    Wraps driver exceptions so the raw message — which can contain schema
    details, table names and occasionally parameter values — never reaches a
    client. The original is logged with full context.
    """

    status_code = 503
    problem_type = "database-error"
    title = "Database unavailable"

    def __init__(self, detail: str = "The analytics database is unavailable.") -> None:
        """Initialise the error.

        Args:
            detail: Deliberately generic message for the caller.
        """
        super().__init__(detail, headers={"Retry-After": "10"})


class QueryTimeoutError(DatabaseError):
    """A query exceeded ``PRISM_DB__STATEMENT_TIMEOUT_MS``.

    Distinguished from a general database failure because it is actionable: the
    caller should narrow the date range or the filter set.
    """

    status_code = 504
    problem_type = "query-timeout"
    title = "Query timeout"

    def __init__(self, timeout_ms: int) -> None:
        """Initialise the error.

        Args:
            timeout_ms: The configured statement timeout.
        """
        super().__init__(
            f"The query exceeded the {timeout_ms}ms statement timeout. "
            "Narrow the date range or reduce the number of filters."
        )


class StaleAnalyticsError(PrismError):
    """A materialized view has never been populated.

    Fires when a query reads ``analytics.mv_*`` before the first refresh, which
    in practice means the database was migrated but never seeded. Worth its own
    error because the honest fix is a documented command, and a generic 500 would
    send the reader hunting through logs for it.
    """

    status_code = 503
    problem_type = "stale-analytics"
    title = "Analytics views not populated"

    def __init__(self, view: str | None = None) -> None:
        """Initialise the error.

        Args:
            view: The unpopulated view, when known.
        """
        target = f"analytics.{view}" if view else "the analytics materialized views"
        super().__init__(
            f"{target} has not been populated. Run `make seed` to generate data, "
            "or `make refresh` if data already exists."
        )


__all__ = [
    "PROBLEM_BASE_URI",
    "DatabaseError",
    "NotFoundError",
    "PrismError",
    "QueryNotFoundError",
    "QueryTimeoutError",
    "RateLimitError",
    "StaleAnalyticsError",
    "UnauthorizedError",
    "UnknownDimensionValueError",
    "ValidationError",
]
