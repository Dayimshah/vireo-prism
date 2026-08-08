"""Authentication for the one privileged endpoint, and shared input hardening.

Stated plainly, because a reviewer will ask: **the read endpoints have no
authentication.** That is deliberate. Prism is a portfolio analytics service over
entirely synthetic data, and putting a login wall in front of it would stop a
recruiter from clicking through the dashboard, which is the whole purpose. The
trade-off is recorded in ``docs/decisions.md`` rather than left to be inferred.

What *is* protected is every operation that changes state — currently one:
``POST /api/v1/admin/refresh``, which rebuilds the materialized views. It takes an
``X-API-Key`` header compared in constant time.

This module also holds the identifier validator used wherever a table, view or
column name reaches SQL as a literal. Those places exist — ``REFRESH
MATERIALIZED VIEW`` cannot take a parameter for its view name, and ``ORDER BY``
cannot take one for its column — and each is a potential injection point that
parameter binding cannot cover. :func:`validate_identifier` is the chokepoint.
"""

from __future__ import annotations

import hmac
import re
import secrets
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, Header

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError, ValidationError
from app.core.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

#: A safe SQL identifier: letter or underscore, then letters, digits, underscores.
#: Anything else is rejected outright rather than escaped, because there is no
#: legitimate identifier in this schema that needs quoting.
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

#: Sort directions accepted by paginated endpoints.
_SORT_DIRECTIONS: Final[frozenset[str]] = frozenset({"asc", "desc"})

#: Values that are obviously placeholders rather than real keys. Used only to
#: emit a startup warning; they still authenticate, so local development works.
_PLACEHOLDER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "local-dev-admin-key",
        "change-me",
        "changeme",
        "secret",
        "password",
        "admin",
        "",
    }
)


def check_admin_key_strength() -> None:
    """Warn at startup if the admin key is a known placeholder.

    Called from the FastAPI lifespan. A warning rather than a hard failure: the
    default must keep working for a local ``docker compose up``, and refusing to
    boot would make the project harder to try than to secure.
    """
    settings = get_settings()
    key = settings.api.admin_key

    if key.strip().lower() in _PLACEHOLDER_KEYS:
        logger.warning(
            "admin_key_is_placeholder",
            hint="Set PRISM_API__ADMIN_KEY to a random value before exposing this service.",
            environment=str(settings.env),
        )
    elif len(key) < 32:
        logger.warning("admin_key_short", length=len(key), recommended_min=32)


async def require_admin_key(
    x_api_key: Annotated[
        str | None,
        Header(
            alias="X-API-Key",
            description="Shared secret for privileged operations.",
        ),
    ] = None,
) -> None:
    """Authorise a privileged request.

    Args:
        x_api_key: Value of the ``X-API-Key`` request header.

    Raises:
        UnauthorizedError: If the header is missing or does not match.
    """
    settings = get_settings()

    if x_api_key is None:
        logger.warning("admin_auth_missing_header")
        raise UnauthorizedError("The X-API-Key header is required for this endpoint.")

    # compare_digest is timing-safe. A plain `==` would leak the key's length and
    # its matching prefix through response timing — a real, if slow, attack.
    if not hmac.compare_digest(x_api_key.encode(), settings.api.admin_key.encode()):
        logger.warning("admin_auth_invalid_key")
        raise UnauthorizedError("Invalid API key.")


#: Reusable dependency for privileged routes.
AdminAuth = Annotated[None, Depends(require_admin_key)]


def validate_identifier(value: str, *, kind: str = "identifier") -> str:
    """Validate a SQL identifier that must be interpolated as a literal.

    Use this at every point where a name cannot be a bound parameter: the view
    name in ``REFRESH MATERIALIZED VIEW``, the column in a dynamic ``ORDER BY``,
    a partition name in maintenance DDL.

    Rejection, not escaping, is the strategy. Quoting an arbitrary string is
    error-prone; every legitimate identifier in this schema is a plain
    ``[a-z_][a-z0-9_]*``, so anything else is a bug or an attack and both should
    stop here.

    Args:
        value: The candidate identifier.
        kind: Noun used in the error message, e.g. ``"sort column"``.

    Returns:
        The validated identifier, unchanged.

    Raises:
        ValidationError: If the value is not a plain SQL identifier.
    """
    if not _IDENTIFIER_RE.match(value):
        raise ValidationError(
            f"Invalid {kind}: {value!r}. Expected letters, digits and underscores only."
        )
    return value


def validate_sort_direction(value: str) -> str:
    """Validate a sort direction.

    Args:
        value: Candidate direction, case-insensitive.

    Returns:
        The direction lower-cased.

    Raises:
        ValidationError: If the value is neither ``asc`` nor ``desc``.
    """
    direction = value.strip().lower()
    if direction not in _SORT_DIRECTIONS:
        raise ValidationError(
            f"Invalid sort direction: {value!r}. Expected 'asc' or 'desc'."
        )
    return direction


def build_order_by(
    column: str,
    direction: str,
    *,
    allowed_columns: frozenset[str],
) -> str:
    """Build a validated ``ORDER BY`` fragment.

    PostgreSQL will not accept a bound parameter in ``ORDER BY``, so paginated
    endpoints must interpolate. This function is the only sanctioned way to do
    it, and it checks membership in an explicit allowlist *before* the identifier
    pattern — a name that passes the regex is still refused unless the caller
    declared it sortable.

    Args:
        column: Requested sort column.
        direction: ``asc`` or ``desc``.
        allowed_columns: Columns the calling endpoint permits.

    Returns:
        A fragment such as ``"watch_seconds DESC NULLS LAST"``.

    Raises:
        ValidationError: If the column is not allowed or the direction is invalid.
    """
    if column not in allowed_columns:
        raise ValidationError(
            f"Cannot sort by {column!r}. Allowed: {', '.join(sorted(allowed_columns))}."
        )

    validate_identifier(column, kind="sort column")
    sql_direction = validate_sort_direction(direction).upper()

    # NULLS LAST for both directions. PostgreSQL defaults to NULLS FIRST on DESC,
    # which puts "no data" rows at the top of a leaderboard — technically correct,
    # visibly wrong to anyone reading the dashboard.
    return f"{column} {sql_direction} NULLS LAST"


def generate_admin_key(length: int = 48) -> str:
    """Generate a cryptographically strong admin key.

    Convenience for setup: ``python -c "from app.core.security import
    generate_admin_key; print(generate_admin_key())"``.

    Args:
        length: Approximate character length of the token.

    Returns:
        A URL-safe random token.
    """
    return secrets.token_urlsafe(length)[:length]


__all__ = [
    "AdminAuth",
    "build_order_by",
    "check_admin_key_strength",
    "generate_admin_key",
    "require_admin_key",
    "validate_identifier",
    "validate_sort_direction",
]
