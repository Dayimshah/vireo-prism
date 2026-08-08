"""Shared query-parameter dependencies: windows, filters, limits, cohort floors.

Forty-eight endpoints draw on the same handful of parameter groups. Declared inline
they would be roughly ten parameters per signature repeated forty-eight times, and the
failure mode of that is not verbosity — it is drift. One endpoint ends up accepting
``?country=`` while its neighbour accepts ``?countries=``, and nothing catches it,
because each signature is independently plausible.

Dependencies rather than Pydantic query models
----------------------------------------------
FastAPI 0.115 can take a Pydantic model as a group of query parameters, and it is the
more modern idiom, but it expands that model into real parameters only when it is the
*sole* non-path query parameter of the endpoint. Add anything beside it — a second
model, or a plain ``day_n: int = 1`` — and both collapse into opaque required
parameters named after the arguments, which fail every request with
``{"loc": ["query", "window"], "msg": "Field required"}``. Verified against 0.115.6
rather than inferred.

That matters here because these endpoints are not uniform. Across the 48 there are
sixteen distinct parameter shapes: most take a window and filters, but retention adds
``observation_end``, the segment endpoints add ``segment_by``, cohort adds
``max_months``, experiments add ``alpha`` and ``min_arm_size``. Under the
one-model rule each shape needs its own composed class, and sixteen near-identical
classes reintroduce exactly the drift the sharing was meant to prevent.

``Depends`` callables have no such restriction: several compose in one signature, and
they mix freely with ordinary scalars. So each group is a function whose own signature
declares its parameters, and a router asks for the ones it needs::

    async def dau(session: SessionDep, catalog: CatalogDep,
                  window: WindowDep, filters: FilterDep) -> ...

What that costs, and how it is paid back
----------------------------------------
A Pydantic model can set ``extra="forbid"``, which turns ``?contry=IN`` into a 422
naming the offending parameter. A dependency cannot: unrecognised query parameters are
simply ignored, so a misspelled filter would return an unfiltered result reported as
unfiltered — a plausible chart answering a question nobody asked. That is the same
class of failure as an unvalidated filter value, which
:class:`~app.db.deps.DimensionCatalog` exists to prevent, and it is not acceptable to
lose it as a side effect of an implementation detail.

It is recovered in :mod:`app.middleware`, which rejects any query parameter the matched
route did not declare. One check covers every route, and it cannot fall out of step
with a signature the way a hand-maintained allowlist would.

Parameter names are singular; service fields are plural
-------------------------------------------------------
The query parameter is singular and repeatable — ``?country=IN&country=US`` — matching
both REST convention and the ``"field": "country"`` that
:class:`~app.core.exceptions.UnknownDimensionValueError` already puts in its error
body, so a caller who mistypes a country reads ``field: country`` and finds
``country=`` in their own URL. :class:`~app.services.base.FilterRequest` names the same
things plurally because it holds sequences. :func:`filter_params` is the one place the
two vocabularies meet.

An earlier revision of this module tried to serve both spellings, with plural fields
carrying ``alias="country"`` and ``populate_by_name=True``. It was quietly wrong:
``?countries=India`` was accepted and then *ignored*, because query parsing reads the
alias while ``extra="forbid"`` sees a legitimate field name. A 200 describing the whole
user base while the caller believed they had filtered it is worse than a 422, so there
is now exactly one accepted spelling per parameter and the plural form is a rejected
unknown.

Dates are required
------------------
Neither ``date_from`` nor ``date_to`` has a default, and that is a deliberate cost. A
default of "the last thirty days" would be friendlier on the docs page, but it would be
thirty days relative to *now*, while the dataset ends whenever the seeder last ran. On
a repository cloned six months after its data was generated every chart would open
empty, and a reader would have no way to tell an empty default window from a broken
query. ``GET /api/v1/meta/dataset`` reports the real bounds, so a client can choose a
window that contains data — a better answer than a default that is right only this
week.

Same reasoning as :func:`~app.services.base.resolve_window` declining to clamp: this
layer does not quietly substitute a question the caller did not ask.
"""

from __future__ import annotations

# Imported at runtime, not under TYPE_CHECKING. FastAPI resolves a dependency's
# annotations with `typing.get_type_hints` when it analyses the signature, and with
# `from __future__ import annotations` these are strings it must look up in this
# module's namespace. Under TYPE_CHECKING the name is absent at runtime and the
# application fails at import — the whole service, not one endpoint. Same trap as the
# `date` import in `app/services/experiments.py`.
from datetime import date
from typing import Annotated

from fastapi import Depends, Query

from app.services.base import DateWindow, FilterRequest, resolve_window

# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def window_params(
    date_from: Annotated[
        date,
        Query(description="First day of the reporting window, inclusive (YYYY-MM-DD)."),
    ],
    date_to: Annotated[
        date,
        Query(description="Last day of the reporting window, inclusive (YYYY-MM-DD)."),
    ],
) -> DateWindow:
    """Validate an inclusive reporting window.

    Calls :func:`~app.services.base.resolve_window`, so a backwards or over-wide range
    is a 422 before the endpoint body runs, and the returned window carries
    :attr:`~app.services.base.DateWindow.days` for the response metadata.

    The service called next validates the same dates again. That is intentional
    duplication, not an oversight: the rule lives in the service so it holds for a
    script or a test that never touches HTTP, and re-checking two dates costs nothing.

    Args:
        date_from: First day, inclusive.
        date_to: Last day, inclusive.

    Returns:
        The validated window.

    Raises:
        ValidationError: If the range runs backwards or exceeds
            ``PRISM_API__MAX_DATE_RANGE_DAYS``.
    """
    return resolve_window(date_from, date_to)


def end_date_params(
    date_to: Annotated[
        date,
        Query(
            description=(
                "Last day counted, inclusive (YYYY-MM-DD). Accumulates from the "
                "beginning of the dataset."
            ),
        ),
    ],
) -> date:
    """Take a cut-off date with no start, for the lifetime-to-date metrics.

    Used by the country ranking and the raw experiment counts. Both accumulate from the
    beginning of the dataset, and giving either a start date would quietly turn a
    lifetime figure into a windowed one — the :mod:`app.services` docstring lists the
    functions that deliberately take no range.

    Args:
        date_to: Last day counted, inclusive.

    Returns:
        The date, unchanged. There is nothing to cross-validate against.
    """
    return date_to


def observation_params(
    observation_end: Annotated[
        date | None,
        Query(
            description=(
                "Last day an outcome may be observed. Omit to count every outcome on "
                "record. Set this to hold the observation period equal across cohorts, "
                "so a later cohort is not compared on a shorter follow-up."
            ),
        ),
    ] = None,
) -> date | None:
    """Take the observation cut-off used by the cohort and retention endpoints.

    Args:
        observation_end: Last day an outcome counts, or ``None`` for all of them.

    Returns:
        The date or ``None``.
    """
    return observation_end


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def filter_params(
    country: Annotated[
        list[str] | None,
        Query(description="Country name or ISO code. Repeat for several."),
    ] = None,
    channel: Annotated[
        list[str] | None,
        Query(description="Marketing channel name. Repeat for several."),
    ] = None,
    persona: Annotated[
        list[str] | None,
        Query(description="Persona name. Repeat for several."),
    ] = None,
    device: Annotated[
        list[str] | None,
        Query(description="Device name, matched against the user's signup device."),
    ] = None,
    genre: Annotated[
        list[str] | None,
        Query(description="Genre name. Repeat for several."),
    ] = None,
    content_type: Annotated[
        list[str] | None,
        Query(description="Content type. Repeat for several."),
    ] = None,
    language: Annotated[
        list[str] | None,
        Query(
            description=(
                "Catalogue language. The one filter with no allowlist: an unknown "
                "value narrows the result to nothing rather than raising."
            ),
        ),
    ] = None,
    is_premium: Annotated[
        bool | None,
        Query(
            description=(
                "Restrict to currently paid (true) or currently unpaid (false) users. "
                "Omit for both."
            ),
        ),
    ] = None,
) -> FilterRequest:
    """Collect the eight dimension filters.

    Values are not checked here. :func:`~app.services.base.resolve_filters` owns that,
    and it raises :class:`~app.core.exceptions.UnknownDimensionValueError` naming the
    valid options, so an unknown value is a 422 rather than an empty chart. One
    exception is documented there and worth repeating: ``language`` has no dimension
    table, so a misspelled language yields an empty result rather than an error.

    Args:
        country: Country names or ISO codes. Both forms resolve.
        channel: Marketing channel names.
        persona: Persona names.
        device: Device names, matched against each user's signup device.
        genre: Genre names.
        content_type: ``core.content_type`` enum labels.
        language: Catalogue language names.
        is_premium: Currently paid, currently unpaid, or omitted for both. ``False`` is
            a real filter — only omission disables it.

    Returns:
        The filters as the service layer expects them.
    """
    return FilterRequest(
        countries=country,
        channels=channel,
        personas=persona,
        devices=device,
        genres=genre,
        content_types=content_type,
        languages=language,
        is_premium=is_premium,
    )


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def limit_params(
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "Rows to return. Omit for the query's own default. Values above the "
                "configured maximum are clamped rather than refused."
            ),
        ),
    ] = None,
) -> int | None:
    """Take a row limit for the leaderboard and scorecard endpoints.

    ``ge=1`` rejects zero and negatives at the edge, which
    :func:`~app.services.base.resolve_limit` also does. The asymmetry with the upper
    bound is explained there: asking for more than the maximum means "as many as
    possible" and is clamped, while asking for none is a bug and is refused.

    Args:
        limit: Requested row count, or ``None`` for the query's default.

    Returns:
        The limit or ``None``; the service resolves the default and the ceiling.
    """
    return limit


#: Validated reporting window.
WindowDep = Annotated[DateWindow, Depends(window_params)]

#: Cut-off date with no start, for lifetime-to-date metrics.
EndDateDep = Annotated[date, Depends(end_date_params)]

#: Optional observation cut-off.
ObservationDep = Annotated[date | None, Depends(observation_params)]

#: The eight dimension filters.
FilterDep = Annotated[FilterRequest, Depends(filter_params)]

#: Optional row limit.
LimitDep = Annotated[int | None, Depends(limit_params)]


__all__ = [
    "EndDateDep",
    "FilterDep",
    "LimitDep",
    "ObservationDep",
    "WindowDep",
    "end_date_params",
    "filter_params",
    "limit_params",
    "observation_params",
    "window_params",
]
