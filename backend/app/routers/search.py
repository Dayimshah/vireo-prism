"""Global search across content, users and experiments.

One endpoint, and the only one in this API that takes neither filters nor a date window.
Search is a lookup, not an analysis: it exists so the dashboard can jump to a title or an
experiment by name, and applying an audience filter to a name lookup would silently hide
matches a reader can see exist.

Minimum query length
--------------------
Two characters, enforced here *and* independently in the service layer. A single-character
term matches a large fraction of the catalogue, which is a slow query returning a useless
result set — so it is rejected rather than served.
"""

# No `from __future__ import annotations` here — see the note in `app/routers/kpi.py`.
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.deps import SessionDep
from app.repositories.search import MIN_QUERY_LENGTH
from app.routers.base import respond
from app.schemas import search as schema
from app.schemas.base import DataResponse, with_rate_limit
from app.schemas.params import LimitDep
from app.services import search as service

router = APIRouter(prefix="/search", tags=["Search"], responses=with_rate_limit())


@router.get(
    "",
    response_model=DataResponse[schema.SearchResultRow],
    summary="Search content, users and experiments by name",
)
async def search(
    session: SessionDep,
    q: Annotated[
        str,
        Query(
            min_length=MIN_QUERY_LENGTH,
            max_length=100,
            description="Search term. At least two characters.",
            examples=["thriller"],
        ),
    ],
    limit: LimitDep = None,
) -> DataResponse[schema.SearchResultRow]:
    """Return matching content, users and experiments in one ranked list.

    Results from all three entity types share one envelope, distinguished by
    ``result_type``. Read that column rather than inferring the kind from which fields are
    populated — a union means most rows carry ``None`` for the columns belonging to the
    other types.

    Ranked by match quality, so a truncating ``limit`` keeps the best matches rather than
    an arbitrary slice.
    """
    rows = await service.search(session, q, limit)
    return respond(schema.SearchResultRow, rows)


__all__ = ["router"]
