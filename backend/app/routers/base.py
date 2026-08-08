"""Shared response assembly for every router.

One helper, :func:`respond`, and it exists because forty endpoints otherwise repeat the
same four steps: validate the rows into their model, count them, convert the window into
its echo, and read whether filters were active.

Why not in :mod:`app.schemas.base`
----------------------------------
:func:`~app.schemas.base.build_meta` takes a
:class:`~app.schemas.base.WindowEcho`, while the parameter dependencies yield a
:class:`~app.services.base.DateWindow`. Something has to bridge the two. It is not in the
schemas package because that package is a pure wire contract with no knowledge of how a
route obtains its arguments, and it is not in :mod:`app.routers` because that module
imports every domain router — a domain router importing back from it would be a cycle.

So it sits here, in the routers package, next to its only callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from app.schemas.base import DataResponse, RowModel, ValueResponse, WindowEcho, build_meta

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.services.base import DateWindow, FilterRequest

RowT = TypeVar("RowT", bound=RowModel)
ValueT = TypeVar("ValueT")


def echo(window: DateWindow) -> WindowEcho:
    """Render a validated window for the response envelope.

    Explicit rather than by attribute lookup: ``DateWindow.days`` is a property, and
    naming the three fields keeps the conversion readable in one place.

    Args:
        window: The validated window.

    Returns:
        The window as it appears in ``meta``.
    """
    return WindowEcho(date_from=window.date_from, date_to=window.date_to, days=window.days)


def respond(
    model: type[RowT],
    rows: Sequence[dict[str, Any]],
    *,
    window: DateWindow | None = None,
    filters: FilterRequest | None = None,
) -> DataResponse[RowT]:
    """Wrap query rows in the standard envelope.

    Args:
        model: The row model for this endpoint.
        rows: The rows as the service returned them. Order is preserved: it is
            meaningful for every series and leaderboard in this API.
        window: The validated window, when the endpoint took one.
        filters: The resolved filters, when the endpoint took them.

    Returns:
        The rows validated into ``model``, with metadata.
    """
    return DataResponse[model](  # type: ignore[valid-type]
        data=[model.model_validate(row) for row in rows],
        meta=build_meta(
            rows=len(rows),
            window=echo(window) if window else None,
            filters_applied=bool(filters and filters.is_active),
        ),
    )


def respond_value(
    value: ValueT,
    *,
    rows: int = 1,
    window: DateWindow | None = None,
    filters: FilterRequest | None = None,
) -> ValueResponse[ValueT]:
    """Wrap a single computed object in the standard envelope.

    For the endpoints that return one thing rather than rows — the overview tiles, an
    experiment's results, the filter catalogue.

    Args:
        value: The already-built response model.
        rows: Row count to report. Defaults to 1, the object itself.
        window: The validated window, when the endpoint took one.
        filters: The resolved filters, when the endpoint took them.

    Returns:
        The value with metadata.
    """
    return ValueResponse(
        data=value,
        meta=build_meta(
            rows=rows,
            window=echo(window) if window else None,
            filters_applied=bool(filters and filters.is_active),
        ),
    )


__all__ = ["echo", "respond", "respond_value"]
