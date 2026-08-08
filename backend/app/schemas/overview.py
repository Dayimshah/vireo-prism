"""Response models for the dashboard headline tiles.

Mirrors :class:`~app.services.overview.Overview` and
:class:`~app.services.overview.Tile`. As in :mod:`app.schemas.experiments`, the
computed properties — ``delta``, ``delta_pct``, ``direction``, ``sentiment`` — are
declared as fields and populated by attribute lookup, because a tile without its delta
is half a tile and every client would otherwise recompute the same four values.

Read ``grain`` before comparing tiles
------------------------------------
The tiles are not reduced the same way, and the response says so per tile rather than
leaving it to a footnote. ``avg_dau`` is a **mean** over the window's days — summing
daily DAU would count a daily visitor once per day. ``sessions`` and ``watch_hours``
are **totals**, because event counts add. ``stickiness_pct`` is a mean over the days
where it is defined. ``mrr_usd`` and ``arpu_usd`` are the **latest month**, since MRR is
a recurring stock and summing months is meaningless.

That is why ``revenue_month`` is returned alongside: a window narrower than a month
yields a partial month, and a reader should be able to see which month the revenue
figures describe.

An empty window does not blank the tiles
----------------------------------------
Every query behind the overview builds its own date spine and LEFT JOINs onto it, so a
window entirely outside the dataset returns explicit zeros rather than nothing. Those
tiles read ``0`` with real deltas. Only genuinely undefined figures are ``None`` — the
ratios with an empty denominator, ``stickiness_pct`` and ``arpu_usd``.

``delta_pct`` is also ``None`` when the previous window was zero. Growth from nothing
has no percentage, and inventing one would put an arbitrary number on a chart;
``delta`` still carries the absolute movement.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from app.schemas.base import Number, PrismModel, WindowEcho

# Runtime imports: these enums appear in field annotations, which Pydantic resolves
# when it builds each model's validator.
from app.services.overview import (  # noqa: TC001
    Direction,
    Grain,
    Sentiment,
    Unit,
)

if TYPE_CHECKING:
    from app.services.base import DateWindow
    from app.services.overview import Overview, Tile


class TileSchema(PrismModel):
    """One headline number with its comparison against the preceding window.

    Attributes:
        key: Stable identifier, e.g. ``avg_dau``. Key off this; the label is free to be
            reworded.
        label: Human-readable name.
        value: The figure for the requested window, or ``None`` when undefined.
        previous: The same figure over the preceding window, or ``None``.
        unit: What the number measures.
        grain: How it was reduced from its series — read this before comparing one tile
            against another.
        higher_is_better: Whether an increase is desirable. Drives ``sentiment``.
        description: One line on how the figure was derived, for the tile's tooltip, so
            the reduction is visible where the number is read.
        delta: ``value - previous``, or ``None`` when either side is missing — which is
            a different statement from a change of zero.
        delta_pct: The change as a percentage of ``previous``. ``None`` when no
            comparison is possible, and also when ``previous`` is zero.
        direction: Which way the tile moved: up, down, flat, or unknown.
        sentiment: Whether that movement is good news, given ``higher_is_better``. A
            fall in churn and a rise in revenue are both ``good``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    key: str
    label: str
    value: Number | None = None
    previous: Number | None = None
    unit: Unit
    grain: Grain = Field(
        description="How the figure was reduced. Tiles of different grain are not comparable.",
    )
    higher_is_better: bool = True
    description: str = ""
    delta: Number | None = None
    delta_pct: Number | None = Field(
        default=None,
        description="Null when previous is zero: growth from nothing has no percentage.",
    )
    direction: Direction
    sentiment: Sentiment

    @classmethod
    def from_tile(cls, tile: Tile) -> TileSchema:
        """Build from the service-layer dataclass.

        Args:
            tile: The computed tile.

        Returns:
            The response model, including the four computed properties.
        """
        return cls.model_validate(tile)


class OverviewSchema(PrismModel):
    """The full tile set for one window, with the basis it was compared against.

    Attributes:
        window: The requested window, as validated.
        comparison_window: The equally long window immediately before it. Equal length
            matters: comparing 30 days against a calendar month would move the delta
            whenever the month had 31 days, which reads as a trend and is an artefact.
        revenue_month: First day of the month the revenue tiles were taken from, or
            ``None`` when the window covered no month with revenue. Returned so a
            reader can see whether that month is complete.
        is_filtered: Whether filters were applied, so a chart can say the tiles describe
            a segment rather than the whole base.
        tiles: The tiles, in display order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: WindowEcho
    comparison_window: WindowEcho
    revenue_month: date | None = None
    is_filtered: bool
    tiles: list[TileSchema] = Field(default_factory=list)

    @classmethod
    def from_overview(cls, overview: Overview) -> OverviewSchema:
        """Build from the service-layer dataclass.

        The two windows are converted explicitly rather than by attribute lookup:
        :class:`~app.services.base.DateWindow` carries ``days`` as a property, and
        being explicit here keeps the conversion in one readable place.

        Args:
            overview: The computed overview.

        Returns:
            The response model.
        """

        def echo(window: DateWindow) -> WindowEcho:
            """Render one window for the response."""
            return WindowEcho(
                date_from=window.date_from,
                date_to=window.date_to,
                days=window.days,
            )

        return cls(
            window=echo(overview.window),
            comparison_window=echo(overview.comparison_window),
            revenue_month=overview.revenue_month,
            is_filtered=overview.is_filtered,
            tiles=[TileSchema.from_tile(tile) for tile in overview.tiles],
        )


__all__ = [
    "OverviewSchema",
    "TileSchema",
]
