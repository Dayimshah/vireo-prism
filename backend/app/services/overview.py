"""The dashboard's headline tiles, each with a period-over-period comparison.

The only service that composes other services rather than wrapping a repository.
It runs three service calls over the requested window, the same three over the
equally long window immediately before it, and reduces both to a handful of scalars
with a delta between them.

It does not cache its own result
--------------------------------
Every value here comes from :mod:`app.services.kpi` and
:mod:`app.services.monetization`, which cache already. Caching the composite on top
would store the same numbers a second time under a key nothing else shares, and the
reduction is arithmetic over a few hundred rows. Leaving it uncached means a
dashboard showing both these tiles and the underlying charts reads one cache entry
per query instead of two, and it keeps the ``X-Cache`` tally meaningful — six
lookups, so ``PARTIAL`` is a genuine and expected state here rather than a symptom.

Six queries, not three
----------------------
A delta needs a basis, and the basis is a second full pass over the preceding
window. There is no cheaper honest option: the comparison figures are not derivable
from the current window's rows. :meth:`~app.services.base.DateWindow.preceding`
supplies a window of exactly equal length, which matters — comparing 30 days against
a calendar month would move the delta whenever the month had 31 days, and that reads
as a trend.

Aggregation is per metric, because the metrics are not the same kind of number
-----------------------------------------------------------------------------
This is the part worth reading before trusting a tile.

*Active users cannot be summed.* Adding 31 daily DAU figures counts a user who
visited every day 31 times. The honest window-level figure is a distinct count over
the whole window, which the daily series cannot produce, so the tile reports the
**mean** daily DAU and says so. A "total active users" tile would need its own query.

*Sessions and watch time can be summed.* They are event counts, so they add.

*Stickiness is a ratio, so it is averaged, not summed* — and averaged only over the
days where it is defined. The SQL returns ``None`` where MAU is zero; counting those
as zero would pull the average down for a reason that has nothing to do with
engagement.

*MRR and ARPU are month-grained stocks, so the latest month is taken.* Summing MRR
across months is meaningless: it is a recurring monthly figure, not a flow, and
adding January's to February's describes nothing. The consequence is that these two
tiles carry a different grain from the rest, which
:attr:`Tile.grain` states explicitly — and that a window narrower than a month
yields a *partial* month, whose MRR is genuinely lower than the month will finish at.
:attr:`Overview.revenue_month` names the month the figures came from so a reader can
see which one they are looking at.

Undefined stays undefined, and zero is not undefined
----------------------------------------------------
A tile whose metric produced no usable number reports ``None``, not zero, and every
delta derived from it is ``None`` too — which is not the statement "no change" and
must not render as one. A percentage change against a previous value of zero is
likewise undefined rather than infinite, so :attr:`Tile.delta_pct` is ``None`` there
while :attr:`Tile.delta` still carries the absolute movement.

What that does *not* mean is that an empty window blanks the tiles. Every query
behind them builds its own spine — the engagement queries a daily one, the ARPU query
a monthly one — and LEFT JOINs the data onto it. A window lying entirely outside the
dataset therefore returns rows of explicit zeros rather than no rows, so those tiles
read ``0`` and their deltas are real movements from a genuinely empty period. That is
the honest rendering: nothing happened, and zero is the measurement.

``None`` is reserved for the cases where the underlying figure is genuinely
undefined — a ratio whose denominator is empty, which is why stickiness is averaged
only over the days the SQL reports it, and why the revenue pair goes blank if the
window covers no month at all rather than a month with no revenue in it.

Direction is not sentiment
--------------------------
Every tile here improves as it rises, but :class:`Tile` carries
:attr:`~Tile.higher_is_better` rather than assuming that, so the first tile that
inverts it — churned MRR, cancellations — cannot be quietly coloured green for
getting worse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date  # noqa: TC003 — see the note in app/services/experiments.py
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.services import kpi, monetization
from app.services.base import DateWindow, FilterRequest, resolve_window

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.deps import DimensionCatalog

#: Seconds in an hour, for the watch-time tile.
_SECONDS_PER_HOUR = Decimal(3600)

#: Rounding templates, one per unit rather than a single global precision, so a
#: currency tile does not report thousandths of a cent and a stickiness figure is not
#: quoted to four decimal places it does not have.
_PCT_PLACES = Decimal("0.1")
_MONEY_PLACES = Decimal("0.01")
_HOUR_PLACES = Decimal("0.1")
_MEAN_PLACES = Decimal("0.1")


class Unit(StrEnum):
    """What a tile's number measures, so the frontend need not match on labels.

    Attributes:
        USERS: A count of people.
        SESSIONS: A count of sessions.
        HOURS: Duration in hours.
        PERCENT: A percentage, already scaled to 0-100.
        USD: Currency.
    """

    USERS = "users"
    SESSIONS = "sessions"
    HOURS = "hours"
    PERCENT = "percent"
    USD = "usd"


class Grain(StrEnum):
    """How a tile's value was reduced from its series.

    Attributes:
        WINDOW_TOTAL: Summed across every day in the window.
        WINDOW_MEAN: Averaged across the days where the metric is defined.
        LATEST_MONTH: Taken from the most recent month the window covers. Carries the
            partial-month caveat described in the module docstring.
    """

    WINDOW_TOTAL = "window_total"
    WINDOW_MEAN = "window_mean"
    LATEST_MONTH = "latest_month"


class Direction(StrEnum):
    """Which way a tile moved against its comparison window.

    Attributes:
        UP: Increased.
        DOWN: Decreased.
        FLAT: Did not change.
        UNKNOWN: No comparison was possible — one of the two windows had no data.
    """

    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    UNKNOWN = "unknown"


class Sentiment(StrEnum):
    """Whether a tile's movement is good news, given the metric's polarity.

    Attributes:
        GOOD: Moved in the desirable direction.
        BAD: Moved in the undesirable direction.
        NEUTRAL: Did not move.
        UNKNOWN: No comparison was possible.
    """

    GOOD = "good"
    BAD = "bad"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Tile:
    """One headline number with its comparison against the preceding window.

    Attributes:
        key: Stable identifier, e.g. ``"avg_dau"``. The frontend keys off this; the
            label is free to be reworded.
        label: Human-readable name.
        value: The figure for the requested window. ``None`` when the window
            contained no data for this metric.
        previous: The same figure over the preceding window, or ``None``.
        unit: What the number measures.
        grain: How it was reduced from its series.
        higher_is_better: Whether an increase is desirable. Drives
            :attr:`sentiment`.
        description: One line on how the figure was derived, carried through to the
            tile's tooltip so the reduction is visible where the number is read.
    """

    key: str
    label: str
    value: Decimal | None
    previous: Decimal | None
    unit: Unit
    grain: Grain
    higher_is_better: bool = True
    description: str = ""

    @property
    def delta(self) -> Decimal | None:
        """Return the absolute change from :attr:`previous` to :attr:`value`.

        ``None`` when either side is missing, which is a different statement from a
        change of zero.
        """
        if self.value is None or self.previous is None:
            return None
        return self.value - self.previous

    @property
    def delta_pct(self) -> Decimal | None:
        """Return the change as a percentage of :attr:`previous`.

        ``None`` when no comparison is possible, and also when ``previous`` is zero
        — growth from nothing has no percentage, and reporting one would mean
        inventing a denominator. :attr:`delta` still carries the absolute movement in
        that case.
        """
        delta = self.delta
        if delta is None or not self.previous:
            return None
        return _quantize(delta / self.previous * 100, _PCT_PLACES)

    @property
    def direction(self) -> Direction:
        """Return which way the tile moved."""
        delta = self.delta
        if delta is None:
            return Direction.UNKNOWN
        if delta > 0:
            return Direction.UP
        if delta < 0:
            return Direction.DOWN
        return Direction.FLAT

    @property
    def sentiment(self) -> Sentiment:
        """Return whether the movement is good news for this metric."""
        direction = self.direction
        if direction is Direction.UNKNOWN:
            return Sentiment.UNKNOWN
        if direction is Direction.FLAT:
            return Sentiment.NEUTRAL
        rising = direction is Direction.UP
        return Sentiment.GOOD if rising == self.higher_is_better else Sentiment.BAD


@dataclass(frozen=True, slots=True)
class Overview:
    """The full tile set for one window, with the basis it was compared against.

    Attributes:
        window: The requested window.
        comparison_window: The equally long window immediately before it.
        revenue_month: First day of the month the revenue tiles were taken from, or
            ``None`` when the window covered no month with revenue. Returned so a
            reader can see whether that month is complete.
        is_filtered: Whether filters were applied, so a chart can say the tiles
            describe a segment rather than the whole base.
        tiles: The tiles, in display order.
    """

    window: DateWindow
    comparison_window: DateWindow
    revenue_month: date | None
    is_filtered: bool
    tiles: list[Tile] = field(default_factory=list)

    def tile(self, key: str) -> Tile | None:
        """Return one tile by key.

        Args:
            key: The tile's stable identifier.

        Returns:
            The tile, or ``None`` if no tile carries that key.
        """
        return next((tile for tile in self.tiles if tile.key == key), None)


# ---------------------------------------------------------------------------
# Reduction helpers
# ---------------------------------------------------------------------------


def _as_decimal(value: Any) -> Decimal | None:
    """Coerce one cell to a :class:`~decimal.Decimal`.

    Args:
        value: A cell from a query row.

    Returns:
        The value as a ``Decimal``, or ``None`` if it is missing or not numeric.
        Booleans are rejected: ``bool`` is a subclass of ``int``, and averaging a
        flag column would silently produce a rate.

    Note:
        Floats go through ``str`` rather than ``Decimal(float)`` so that ``0.1``
        stays ``0.1`` instead of becoming its exact binary expansion.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    return None


def _quantize(value: Decimal, places: Decimal) -> Decimal:
    """Round a value to a fixed number of decimal places.

    Args:
        value: The value to round.
        places: An exponent template, e.g. ``Decimal("0.01")``.

    Returns:
        The rounded value, or the value unchanged if it cannot be represented at
        that precision — which for the magnitudes here would mean something has
        already gone wrong upstream, and losing the number would hide it.
    """
    try:
        return value.quantize(places)
    except (InvalidOperation, ValueError):
        return value


def _rounded(value: Decimal | None, places: Decimal) -> Decimal | None:
    """Round a value that may be undefined.

    Args:
        value: The value to round, or ``None``.
        places: An exponent template.

    Returns:
        The rounded value, or ``None`` — which must survive rounding rather than
        becoming zero, since the two mean different things on every tile here.
    """
    return None if value is None else _quantize(value, places)


def _total(rows: Sequence[dict[str, Any]], key: str) -> Decimal | None:
    """Sum one column across rows, skipping undefined cells.

    Args:
        rows: Query rows.
        key: Column name.

    Returns:
        The sum, or ``None`` when the column held no numeric value at all —
        distinguishing "no data" from a genuine total of zero.
    """
    values = [number for row in rows if (number := _as_decimal(row.get(key))) is not None]
    return sum(values, Decimal(0)) if values else None


def _mean(rows: Sequence[dict[str, Any]], key: str) -> Decimal | None:
    """Average one column across the rows where it is defined.

    Args:
        rows: Query rows.
        key: Column name.

    Returns:
        The mean, or ``None`` when the column held no numeric value. The denominator
        counts only defined cells — see the module docstring on stickiness.
    """
    values = [number for row in rows if (number := _as_decimal(row.get(key))) is not None]
    if not values:
        return None
    return sum(values, Decimal(0)) / len(values)


def _latest_row(rows: Sequence[dict[str, Any]], order_key: str) -> dict[str, Any] | None:
    """Return the row with the greatest value in ``order_key``.

    The queries order ascending already, so this is belt-and-braces — but "latest"
    is a semantic the caller depends on, and deriving it from row position would
    make a future ``ORDER BY`` change silently wrong instead of loudly wrong.

    Args:
        rows: Query rows.
        order_key: Column to order by, typically ``"month"``.

    Returns:
        The latest row, or ``None`` if there are none with a usable ordering value.
    """
    candidates = [row for row in rows if row.get(order_key) is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row[order_key])


def _hours(seconds: Decimal | None) -> Decimal | None:
    """Convert seconds to hours, rounded for display.

    Args:
        seconds: A duration in seconds, or ``None``.

    Returns:
        The duration in hours, or ``None``.
    """
    if seconds is None:
        return None
    return _quantize(seconds / _SECONDS_PER_HOUR, _HOUR_PLACES)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """The reduced figures for a single window, before any comparison.

    An internal shape: gathering one window's numbers and differencing two windows
    are separate steps, and keeping them separate means the reduction is written once
    rather than once per window.

    Attributes:
        avg_dau: Mean daily active users.
        sessions: Total sessions.
        watch_hours: Total watch time in hours.
        stickiness_pct: Mean DAU/MAU, over the days where it is defined.
        mrr_usd: MRR in the latest month the window covers.
        arpu_usd: ARPU in that same month.
        revenue_month: First day of that month, or ``None``.
    """

    avg_dau: Decimal | None
    sessions: Decimal | None
    watch_hours: Decimal | None
    stickiness_pct: Decimal | None
    mrr_usd: Decimal | None
    arpu_usd: Decimal | None
    revenue_month: date | None


async def _snapshot(
    session: AsyncSession,
    catalog: DimensionCatalog,
    window: DateWindow,
    filters: FilterRequest | None,
) -> _Snapshot:
    """Reduce one window to the scalars the tiles need.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        window: An already-validated window.
        filters: Optional filters, passed through unchanged.

    Returns:
        The reduced figures. Every field is independently ``None``-able: a window can
        hold activity but no revenue, and the revenue tiles should go blank without
        taking the engagement tiles with them.
    """
    # The services revalidate this window. That is harmless and deliberate: they are
    # the public entry points and must validate for every caller, and `preceding()`
    # returns a window of identical length running forwards, so it cannot fail a
    # check the requested window passed.
    daily = await kpi.get_dau(session, catalog, window.date_from, window.date_to, filters)
    sticky = await kpi.get_stickiness(session, catalog, window.date_from, window.date_to, filters)
    revenue = await monetization.get_arpu_trend(
        session, catalog, window.date_from, window.date_to, filters
    )

    latest = _latest_row(revenue, "month")
    mrr = _as_decimal(latest.get("mrr_usd")) if latest else None
    arpu = _as_decimal(latest.get("arpu_usd")) if latest else None

    return _Snapshot(
        avg_dau=_mean(daily, "dau"),
        sessions=_total(daily, "sessions"),
        watch_hours=_hours(_total(daily, "watch_seconds")),
        stickiness_pct=_mean(sticky, "stickiness_pct"),
        mrr_usd=_rounded(mrr, _MONEY_PLACES),
        arpu_usd=_rounded(arpu, _MONEY_PLACES),
        revenue_month=latest.get("month") if latest else None,
    )


async def get_overview(
    session: AsyncSession,
    catalog: DimensionCatalog,
    date_from: date,
    date_to: date,
    filters: FilterRequest | None = None,
) -> Overview:
    """Return the headline tiles for a window, each against the preceding window.

    Args:
        session: A read-only session.
        catalog: The loaded dimension catalogue.
        date_from: First day of the window, inclusive.
        date_to: Last day of the window, inclusive.
        filters: Optional filters as caller-supplied strings, applied identically to
            both windows.

    Returns:
        The tile set. Read :attr:`Tile.grain` before comparing tiles against each
        other: the revenue pair is month-grained while the rest cover the window.

    Raises:
        ValidationError: If the window runs backwards or exceeds the configured
            maximum. Raised before any query runs, so an invalid window costs
            nothing and never depends on cache state.
        UnknownDimensionValueError: If a filter value is unknown.
    """
    window = resolve_window(date_from, date_to)
    comparison = window.preceding()

    current = await _snapshot(session, catalog, window, filters)
    previous = await _snapshot(session, catalog, comparison, filters)

    # The mean is left unrounded above and rounded here: rounding before the
    # comparison would let two windows that differ slightly report a delta of
    # exactly zero.
    tiles = [
        Tile(
            key="avg_dau",
            label="Average DAU",
            value=_rounded(current.avg_dau, _MEAN_PLACES),
            previous=_rounded(previous.avg_dau, _MEAN_PLACES),
            unit=Unit.USERS,
            grain=Grain.WINDOW_MEAN,
            description=(
                "Mean daily active users across the window. Daily figures cannot be "
                "summed — a user active every day would be counted every day."
            ),
        ),
        Tile(
            key="sessions",
            label="Sessions",
            value=current.sessions,
            previous=previous.sessions,
            unit=Unit.SESSIONS,
            grain=Grain.WINDOW_TOTAL,
            description="Total sessions started in the window.",
        ),
        Tile(
            key="watch_hours",
            label="Watch hours",
            value=current.watch_hours,
            previous=previous.watch_hours,
            unit=Unit.HOURS,
            grain=Grain.WINDOW_TOTAL,
            description="Total watch time in the window, converted from seconds.",
        ),
        Tile(
            key="stickiness_pct",
            label="Stickiness",
            value=_rounded(current.stickiness_pct, _PCT_PLACES),
            previous=_rounded(previous.stickiness_pct, _PCT_PLACES),
            unit=Unit.PERCENT,
            grain=Grain.WINDOW_MEAN,
            description=(
                "Mean DAU as a percentage of rolling 28-day MAU, over the days where "
                "MAU is non-zero."
            ),
        ),
        Tile(
            key="mrr_usd",
            label="MRR",
            value=current.mrr_usd,
            previous=previous.mrr_usd,
            unit=Unit.USD,
            grain=Grain.LATEST_MONTH,
            description=(
                "Monthly recurring revenue in the latest month the window covers. A "
                "window narrower than a month reports that month so far."
            ),
        ),
        Tile(
            key="arpu_usd",
            label="ARPU",
            value=current.arpu_usd,
            previous=previous.arpu_usd,
            unit=Unit.USD,
            grain=Grain.LATEST_MONTH,
            description=(
                "Average revenue per active user in that same month, payers and "
                "non-payers together."
            ),
        ),
    ]

    return Overview(
        window=window,
        comparison_window=comparison,
        revenue_month=current.revenue_month,
        is_filtered=filters is not None and filters.is_active,
        tiles=tiles,
    )


__all__ = [
    "Direction",
    "Grain",
    "Overview",
    "Sentiment",
    "Tile",
    "Unit",
    "get_overview",
]
