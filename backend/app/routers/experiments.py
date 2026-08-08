"""A/B test results: per-variant metrics, and the significance test over them.

Two endpoints on the same data. ``/variants`` returns the raw arms — one row per variant
with its denominator and conversions — and ``/results`` returns the same arms with a
two-sided proportion test per variant against control.

Neither takes a date window. An experiment's window is a property of the experiment, not
of the request: it ran between its own start and end dates, and slicing that by an
arbitrary window would report a fragment of a test as though it were the test.
``observation_end`` is offered instead, which moves the cut-off for counting *outcomes*
while leaving enrolment intact — the honest way to ask "what did we know a month in".

Filtering re-segments a randomised population
---------------------------------------------
Applying any filter breaks the randomisation the experiment relied on: the arms were
balanced across the whole enrolled population, not within a country or a persona. The
response sets ``is_segmented`` so a chart can label the result exploratory rather than
leaving a reader to assume it is the experiment's outcome.

A filter that excludes every enrolled user returns **422**, not 404 — the experiment
exists, the filter was simply too narrow. The service issues a second unfiltered lookup on
the empty path specifically to tell those two cases apart, because both return zero rows
from the query itself. An unknown key is the 404.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.db.deps import CatalogDep, SessionDep
from app.routers.base import respond, respond_value
from app.schemas import experiments as schema
from app.schemas.base import DataResponse, ProblemDetail, ValueResponse, with_rate_limit
from app.schemas.params import FilterDep, ObservationDep
from app.services import experiments as service

router = APIRouter(
    prefix="/experiments",
    tags=["Experiments"],
    responses=with_rate_limit(
        {404: {"model": ProblemDetail, "description": "No experiment with that key"}}
    ),
)

#: Shared across both endpoints: the slug identifying one experiment.
ExperimentKey = Annotated[
    str,
    Path(
        min_length=1,
        max_length=100,
        description="The experiment's stable slug.",
        examples=["paywall-copy-value-first"],
    ),
]


@router.get(
    "",
    response_model=DataResponse[schema.ExperimentSummaryRow],
    summary="Every experiment defined in the dataset",
)
async def list_experiments(session: SessionDep) -> DataResponse[schema.ExperimentSummaryRow]:
    """Return every experiment, running first and then newest.

    The discovery endpoint for the two below. Both are keyed by slug, and without this
    a client had no way to obtain a slug it did not already know — leaving only a
    hardcoded list, which breaks silently on the next reseed, or a text box a reader
    cannot fill in.

    Takes no window and no filters. This reads definitions from ``core.experiments``: a
    reporting window would select tests by a period they were not defined in, and the
    user-scope filters describe people rather than tests. Since the API rejects an
    undeclared query parameter rather than ignoring it, sending either is a 422 rather
    than a silent no-op.

    ``enrolled_users`` is for orientation and is not the denominator of any test — see
    :class:`~app.schemas.experiments.ExperimentSummaryRow`.

    Note:
        The 404 documented on this route comes from the router-level ``responses`` and
        is unreachable here: an empty dataset returns ``200`` with no rows, because
        "there are no experiments" is an answer rather than a missing resource. The
        declaration is shared with the two keyed routes below, where a 404 is real.
    """
    rows = await service.list_experiments(session)
    return respond(schema.ExperimentSummaryRow, rows)


@router.get(
    "/{experiment_key}/variants",
    response_model=DataResponse[schema.VariantMetricRow],
    summary="Per-variant enrolment and conversion counts",
)
async def get_variant_metrics(
    session: SessionDep,
    catalog: CatalogDep,
    experiment_key: ExperimentKey,
    filters: FilterDep,
    observation_end: ObservationDep = None,
) -> DataResponse[schema.VariantMetricRow]:
    """Return one row per variant, control included.

    The numbers behind ``/results`` without the statistics: ``n`` users enrolled,
    ``successes`` who converted on the primary metric, and ``rate_pct`` between them. Use
    ``is_control`` to identify the baseline rather than assuming an ordering or a label —
    the control arm's name is per-experiment.
    """
    rows = await service.get_variant_metrics(
        session, catalog, experiment_key, observation_end, filters
    )
    return respond(schema.VariantMetricRow, rows, filters=filters)


@router.get(
    "/{experiment_key}/results",
    response_model=ValueResponse[schema.ExperimentResultsSchema],
    summary="Significance test per variant against control",
)
async def get_results(
    session: SessionDep,
    catalog: CatalogDep,
    experiment_key: ExperimentKey,
    filters: FilterDep,
    observation_end: ObservationDep = None,
    alpha: Annotated[
        float,
        Query(
            gt=0.0,
            lt=1.0,
            description="Two-sided significance level applied to every variant test.",
        ),
    ] = service.DEFAULT_ALPHA,
) -> ValueResponse[schema.ExperimentResultsSchema]:
    """Return the experiment with a two-sided test per variant.

    Verdicts are two-sided, so a variant that lost significantly is reported as a loser
    rather than folded in with the inconclusive ones. A variant with too few users in
    either arm comes back ``underpowered`` instead of tested — "no significant difference"
    and "not enough data to tell" are different claims, and collapsing them is how a test
    gets read as a null result.

    ``bonferroni_alpha`` is **reported, not applied**. The verdicts above use ``alpha``
    unadjusted. Applying the correction silently would change published verdicts on the
    caller's behalf; omitting it would hide a known multiple-comparison bias. Both numbers
    are returned and the choice stays with the reader.
    """
    summary = await service.get_results(
        session,
        catalog,
        experiment_key,
        observation_end,
        alpha,
        filters=filters,
    )
    return respond_value(
        schema.ExperimentResultsSchema.from_summary(summary),
        filters=filters,
    )


__all__ = ["router"]
