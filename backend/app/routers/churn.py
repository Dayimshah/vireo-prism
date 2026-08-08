"""Churn: why subscriptions end, and which users look likely to end theirs.

Two endpoints that differ in kind. ``/reason-mix`` is aggregate and historical.
``/risk-scorecard`` is the only endpoint in this API that returns **one row per user**,
and it takes no date window at all — a risk score describes a user's state now, so a
window would imply a slicing the metric does not support.

The scorecard is heuristic
--------------------------
``risk_score`` is a weighted rule over observable behaviour — days dormant, session
trend, watch-time decline — not a trained model, and not a probability. It ranks users
against each other usefully; it does not say that a score of 70 means a 70% chance of
churning. A dashboard that presents it as a likelihood is overstating it.

On real users this endpoint would need access control and an audit trail: it is a list of
named individuals with a machine-assigned adverse score, which is the kind of output that
deserves a record of who looked at it. This project has neither, by design — every user is
synthetic. Worth being explicit about, because copying the pattern to real data without
those controls would be a mistake.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.deps import CatalogDep, SessionDep
from app.repositories.churn import DEFAULT_MIN_RISK_SCORE
from app.routers.base import respond
from app.schemas import churn as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import FilterDep, LimitDep, WindowDep
from app.services import churn as service

router = APIRouter(prefix="/churn", tags=["Churn"], responses=with_rate_limit())


@router.get(
    "/reason-mix",
    response_model=DataResponse[schema.ChurnReasonRow],
    summary="Cancellation reasons by share",
)
async def get_reason_mix(
    session: SessionDep,
    catalog: CatalogDep,
    window: WindowDep,
    filters: FilterDep,
) -> DataResponse[schema.ChurnReasonRow]:
    """Return the mix of recorded cancellation reasons.

    Self-reported at cancellation, so this is what users chose to say rather than why they
    left. "Too expensive" is the answer people give when a product was not worth its price
    to them, which is not the same claim as the price being wrong.
    """
    rows = await service.get_reason_mix(session, catalog, window.date_from, window.date_to, filters)
    return respond(schema.ChurnReasonRow, rows, window=window, filters=filters)


@router.get(
    "/risk-scorecard",
    response_model=DataResponse[schema.ChurnRiskRow],
    summary="Per-user churn risk scores",
)
async def get_risk_scorecard(
    session: SessionDep,
    catalog: CatalogDep,
    filters: FilterDep,
    limit: LimitDep = None,
    min_risk_score: Annotated[
        int,
        Query(
            ge=0,
            le=100,
            description="Only return users scoring at or above this. 0 returns everyone scored.",
        ),
    ] = DEFAULT_MIN_RISK_SCORE,
) -> DataResponse[schema.ChurnRiskRow]:
    """Return currently subscribed users ranked by churn risk.

    No date window: the score describes each user's present state. Ordered by score
    descending, so a truncating ``limit`` keeps the highest-risk users rather than an
    arbitrary slice.

    Read ``risk_score`` as a ranking, not a probability — see the module docstring.
    """
    rows = await service.get_risk_scorecard(session, catalog, limit, min_risk_score, filters)
    return respond(schema.ChurnRiskRow, rows, filters=filters)


__all__ = ["router"]
