"""Response model for global search.

One query, one model. Semantics live in :mod:`app.repositories.search`.

Search is navigation, not analysis, and its parameters say so: no dates, no filters, no
dimension catalogue. :mod:`app.services` explains why — restricting results to the
dashboard's active filters would make things appear and disappear for reasons the user
cannot see while typing.

The rows are deliberately uniform across result kinds. A content title, a user and a
genre all arrive as ``result_type`` / ``result_id`` / ``label`` / ``sublabel``, so a
type-ahead component renders one list without branching per kind. What each field
*contains* differs by kind, which is what ``sublabel`` exists to carry.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class SearchResultRow(RowModel):
    """One search hit, in a shape shared by every result kind.

    Attributes:
        result_type: What kind of thing was found, e.g. ``content``.
        result_id: Its surrogate key within that kind. Not unique across kinds — a
            client keying a list should combine it with ``result_type``.
        label: Primary display text, e.g. a title.
        sublabel: Secondary text, whose composition depends on the kind — for content
            it is genre, type and year joined for display.
        score: Match quality, higher being better. Ordering is already applied by the
            query, so a client should preserve the order it received rather than
            re-sorting on this.
    """

    result_type: str
    result_id: int = Field(
        description="Unique within result_type, not across kinds. Key on both.",
    )
    label: str
    sublabel: str
    score: Number


__all__ = ["SearchResultRow"]
