"""Temporal shape of the simulation: hour, weekday, holiday and growth effects.

Everything that makes generated timestamps look like human behaviour rather than
a uniform draw lives here.

The timezone decision, which matters more than it looks
------------------------------------------------------
People watch television in the evening — *their* evening. A user in Mumbai peaks
at 21:00 IST, which is 15:30 UTC; a user in Los Angeles peaks at 21:00 PDT, which
is 04:00 UTC the next day. Both are "evening viewing".

So this module generates every timestamp in the user's **local** time, then
converts to UTC for storage. The alternative — drawing the peak directly in UTC —
is one line shorter and produces a dataset where every country on earth watches
at the same instant. That artefact is immediately visible on an hour-of-day
heatmap, and it is the single most common tell in a synthetic clickstream.

The consequence is that ``analytics.mv_user_daily`` buckets by UTC date while
users behave on local dates, so a late-night session in India lands on the
following UTC day. That is exactly what a real warehouse does, and
``docs/decisions.md`` records it rather than pretending the two agree.

Composition
-----------
The four effects multiply into one intensity used to place sessions::

    intensity = hour_weight(local_hour, weekday)
              * weekday_multiplier(weekday)
              * holiday_multiplier(local_date, country)
              * growth_multiplier(days_into_window)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from random import Random

# ===========================================================================
# Timezone offsets
#
# Fixed whole/half-hour offsets rather than IANA zones. Deliberate: pulling in
# zoneinfo would make the dataset depend on the tzdata version installed on the
# generating machine, and a DST transition would silently shift an hour of
# history between two contributors' runs. Reproducibility beats DST fidelity for
# synthetic data, and the note is in docs/seeder-design.md.
#
# Values are hours east of UTC, keyed by core.countries.name.
# ===========================================================================
COUNTRY_UTC_OFFSET_HOURS: Final[dict[str, float]] = {
    "India": 5.5,
    "United States": -6.0,  # population-weighted across four zones
    "United Kingdom": 0.0,
    "Canada": -5.0,
    "Australia": 10.0,
    "Germany": 1.0,
    "France": 1.0,
    "Japan": 9.0,
    "South Korea": 9.0,
    "Singapore": 8.0,
    "United Arab Emirates": 4.0,
    "Brazil": -3.0,
    "Mexico": -6.0,
    "Spain": 1.0,
    "Italy": 1.0,
    "Netherlands": 1.0,
    "South Africa": 2.0,
    "Indonesia": 7.0,
    "Philippines": 8.0,
    "Nigeria": 1.0,
}

#: Fallback for a country absent from the table above.
DEFAULT_UTC_OFFSET_HOURS: Final[float] = 0.0


# ===========================================================================
# Hour of day
#
# Index 0 = midnight local, index 23 = 23:00 local. Weights are relative.
#
# The shape is a genuine streaming curve, not a single bell: a small commute bump
# in the morning, a real lunchtime lift, a slow afternoon, then the dominant
# 20:00-23:00 prime-time block, and a long thin tail past midnight that is where
# binge sessions actually live.
# ===========================================================================
WEEKDAY_HOUR_WEIGHTS: Final[tuple[float, ...]] = (
    0.34,  # 00
    0.19,  # 01
    0.10,  # 02
    0.06,  # 03
    0.05,  # 04
    0.07,  # 05
    0.16,  # 06
    0.34,  # 07  commute
    0.42,  # 08  commute peak
    0.38,  # 09
    0.36,  # 10
    0.41,  # 11
    0.58,  # 12  lunch
    0.62,  # 13  lunch peak
    0.47,  # 14
    0.44,  # 15
    0.51,  # 16
    0.68,  # 17  commute home
    0.92,  # 18
    1.34,  # 19
    1.86,  # 20  prime time begins
    2.00,  # 21  peak
    1.72,  # 22
    1.05,  # 23
)

#: Weekends flatten and broaden: mornings are livelier, the afternoon does not
#: collapse, and the evening peak is earlier and less sharp because it competes
#: with going out.
WEEKEND_HOUR_WEIGHTS: Final[tuple[float, ...]] = (
    0.58,  # 00  Friday and Saturday nights run long
    0.41,  # 01
    0.24,  # 02
    0.12,  # 03
    0.07,  # 04
    0.07,  # 05
    0.11,  # 06
    0.19,  # 07
    0.31,  # 08
    0.52,  # 09  lazy weekend morning
    0.71,  # 10
    0.83,  # 11
    0.94,  # 12
    0.98,  # 13
    0.96,  # 14  afternoon holds up
    0.99,  # 15
    1.08,  # 16
    1.22,  # 17
    1.44,  # 18
    1.68,  # 19
    1.82,  # 20  earlier, softer peak
    1.79,  # 21
    1.58,  # 22
    1.16,  # 23
)

#: Session-volume multiplier by weekday, Monday = 0.
#:
#: Friday and Saturday carry the week. Monday is the trough, which is a real and
#: consistent pattern in consumer streaming.
WEEKDAY_MULTIPLIER: Final[tuple[float, ...]] = (
    0.84,  # Monday
    0.88,  # Tuesday
    0.93,  # Wednesday
    0.98,  # Thursday
    1.28,  # Friday
    1.46,  # Saturday
    1.31,  # Sunday
)

#: Weekday indices treated as weekend for hour-shape purposes. Friday evening
#: behaves like a weekend evening even though Friday is a working day, so it is
#: handled by :func:`hour_weights_for` rather than by this set.
WEEKEND_DAYS: Final[frozenset[int]] = frozenset({5, 6})


# ===========================================================================
# Holidays
#
# Six named windows, each a multiplier over a date range. Two are global and four
# are regional, because a Diwali spike that appears in Germany is a bug a
# reviewer will notice on the country breakdown.
#
# Dates are (month, day) so they recur for any window the profile covers.
# Movable feasts (Diwali, Eid) are pinned to a representative fixed date; the
# alternative is a lunar calendar dependency for a synthetic dataset.
# ===========================================================================


class Holiday:
    """A recurring seasonal spike.

    Attributes:
        name: Label used in the data-quality report.
        start: ``(month, day)`` the window opens.
        end: ``(month, day)`` the window closes, inclusive. May wrap the year.
        multiplier: Session-volume multiplier inside the window.
        countries: Countries affected, or ``None`` for global.
    """

    __slots__ = ("countries", "end", "multiplier", "name", "start")

    def __init__(
        self,
        name: str,
        start: tuple[int, int],
        end: tuple[int, int],
        multiplier: float,
        countries: frozenset[str] | None = None,
    ) -> None:
        """Initialise the holiday window.

        Args:
            name: Label used in the data-quality report.
            start: ``(month, day)`` the window opens.
            end: ``(month, day)`` the window closes, inclusive.
            multiplier: Session-volume multiplier inside the window.
            countries: Affected countries, or ``None`` for global.
        """
        self.name = name
        self.start = start
        self.end = end
        self.multiplier = multiplier
        self.countries = countries

    def covers(self, day: date, country: str) -> bool:
        """Return whether this holiday applies on a given local date.

        Args:
            day: Local date being evaluated.
            country: The user's country name.

        Returns:
            True when the date falls inside the window and the country is affected.
        """
        if self.countries is not None and country not in self.countries:
            return False

        key = (day.month, day.day)
        if self.start <= self.end:
            return self.start <= key <= self.end
        # Window wraps the new year, e.g. 20 December to 2 January.
        return key >= self.start or key <= self.end


_INDIA: Final[frozenset[str]] = frozenset({"India"})
_MEA: Final[frozenset[str]] = frozenset(
    {"United Arab Emirates", "Indonesia", "Nigeria", "India"}
)
_LATAM: Final[frozenset[str]] = frozenset({"Brazil", "Mexico"})
_EAST_ASIA: Final[frozenset[str]] = frozenset({"Japan", "South Korea", "Singapore"})

HOLIDAYS: Final[tuple[Holiday, ...]] = (
    Holiday(
        "Year-end holidays",
        start=(12, 20),
        end=(1, 2),
        multiplier=1.62,
        countries=None,  # global: the largest single spike in the dataset
    ),
    Holiday(
        "Diwali week",
        start=(11, 1),
        end=(11, 8),
        multiplier=1.48,
        countries=_INDIA,
    ),
    Holiday(
        "Eid al-Fitr",
        start=(4, 8),
        end=(4, 14),
        multiplier=1.39,
        countries=_MEA,
    ),
    Holiday(
        "Carnival",
        start=(2, 10),
        end=(2, 16),
        multiplier=1.34,
        countries=_LATAM,
    ),
    Holiday(
        "Golden Week",
        start=(4, 29),
        end=(5, 5),
        multiplier=1.41,
        countries=_EAST_ASIA,
    ),
    Holiday(
        "Mid-August lull",
        start=(8, 8),
        end=(8, 20),
        # Below 1.0 on purpose. Not every seasonal effect is a spike, and a
        # European summer trough is real. Having one negative season stops the
        # holiday chart reading as a series of identical bumps.
        multiplier=0.82,
        countries=frozenset(
            {"Germany", "France", "Italy", "Spain", "Netherlands", "United Kingdom"}
        ),
    ),
)


# ===========================================================================
# Growth
# ===========================================================================

#: Compound monthly growth in session volume across the window. 3.1% per month
#: compounds to roughly 1.7x over eighteen months, which reads as a healthy but
#: not implausible growth curve on the executive dashboard.
MONTHLY_GROWTH_RATE: Final[float] = 0.031

#: Amplitude of a slow sinusoidal wobble layered over the growth trend, as a
#: fraction. Without it the DAU line is suspiciously smooth; real metrics
#: oscillate around their trend.
GROWTH_WOBBLE_AMPLITUDE: Final[float] = 0.045

#: Period of that wobble, in days.
GROWTH_WOBBLE_PERIOD_DAYS: Final[float] = 47.0

#: Per-day multiplicative noise, as a standard deviation. Applied last, so no two
#: days are identical even after every structural effect agrees.
DAILY_NOISE_SIGMA: Final[float] = 0.055


def utc_offset(country: str) -> timedelta:
    """Return a country's fixed offset from UTC.

    Args:
        country: Country name as stored in ``core.countries``.

    Returns:
        The offset as a :class:`~datetime.timedelta`.
    """
    hours = COUNTRY_UTC_OFFSET_HOURS.get(country, DEFAULT_UTC_OFFSET_HOURS)
    return timedelta(hours=hours)


def hour_weights_for(weekday: int) -> tuple[float, ...]:
    """Return the hour-of-day weights for a weekday.

    Friday is given the weekend shape. Its *evening* behaves like Saturday's even
    though its daytime does not, and using the weekend curve is the closer of the
    two available approximations.

    Args:
        weekday: ``date.weekday()`` value, Monday = 0.

    Returns:
        Twenty-four relative weights, index = local hour.
    """
    if weekday in WEEKEND_DAYS or weekday == 4:
        return WEEKEND_HOUR_WEIGHTS
    return WEEKDAY_HOUR_WEIGHTS


def weekday_multiplier(weekday: int) -> float:
    """Return the session-volume multiplier for a weekday.

    Args:
        weekday: ``date.weekday()`` value, Monday = 0.

    Returns:
        The multiplier.
    """
    return WEEKDAY_MULTIPLIER[weekday]


def holiday_multiplier(day: date, country: str) -> float:
    """Return the combined holiday multiplier for a local date.

    Overlapping windows compose multiplicatively. In practice overlaps are rare
    by construction, but composing is the correct behaviour and avoids an
    arbitrary "first match wins" rule.

    Args:
        day: Local date.
        country: The user's country name.

    Returns:
        The multiplier; ``1.0`` when no holiday applies.
    """
    multiplier = 1.0
    for holiday in HOLIDAYS:
        if holiday.covers(day, country):
            multiplier *= holiday.multiplier
    return multiplier


def active_holidays(day: date, country: str) -> list[str]:
    """Return the names of holidays active on a local date.

    Used by the data-quality report to annotate spikes on the volume chart, which
    is how a reader verifies that the December peak is intentional.

    Args:
        day: Local date.
        country: The user's country name.

    Returns:
        Holiday names, possibly empty.
    """
    return [holiday.name for holiday in HOLIDAYS if holiday.covers(day, country)]


def growth_multiplier(days_into_window: int) -> float:
    """Return the trend multiplier for a point in the window.

    Compound growth plus a slow sinusoidal wobble.

    Args:
        days_into_window: Whole days since the window opened.

    Returns:
        The multiplier.
    """
    import math

    months = days_into_window / 30.44
    trend = (1.0 + MONTHLY_GROWTH_RATE) ** months
    wobble = 1.0 + GROWTH_WOBBLE_AMPLITUDE * math.sin(
        2.0 * math.pi * days_into_window / GROWTH_WOBBLE_PERIOD_DAYS
    )
    return trend * wobble


def day_intensity(
    rng: Random,
    local_day: date,
    country: str,
    days_into_window: int,
) -> float:
    """Return the composed session-volume intensity for one local day.

    Multiplies the weekday, holiday and growth effects, then applies
    log-normal noise so the result is never negative and the multiplicative
    structure is preserved.

    Args:
        rng: Seeded random source.
        local_day: The user's local date.
        country: The user's country name.
        days_into_window: Whole days since the window opened.

    Returns:
        A strictly positive intensity, centred near 1.0 early in the window.
    """
    base = (
        weekday_multiplier(local_day.weekday())
        * holiday_multiplier(local_day, country)
        * growth_multiplier(days_into_window)
    )
    # Log-normal rather than additive Gaussian: guarantees positivity and keeps
    # the noise proportional to the level, which is how real metric noise behaves.
    noise = rng.lognormvariate(0.0, DAILY_NOISE_SIGMA)
    return base * noise


def draw_local_hour(rng: Random, weekday: int) -> int:
    """Draw a local hour of day from the appropriate curve.

    Args:
        rng: Seeded random source.
        weekday: ``date.weekday()`` value, Monday = 0.

    Returns:
        An hour in ``[0, 23]``.
    """
    weights = hour_weights_for(weekday)
    return rng.choices(range(24), weights=weights, k=1)[0]


def draw_session_start(
    rng: Random,
    local_day: date,
    country: str,
    *,
    not_after: datetime | None = None,
) -> datetime:
    """Draw a timezone-aware UTC session start for a given local day.

    The hour comes from the local-time curve; minute and second are uniform
    within it. The result is converted to UTC for storage.

    Args:
        rng: Seeded random source.
        local_day: The user's local date.
        country: The user's country name, used for the offset.
        not_after: Hard ceiling in UTC. Supplied as the window end so no event can
            violate the ``event_time <= now()`` CHECK constraint; when the drawn
            time exceeds it, the time is pulled back rather than the day skipped.

    Returns:
        A timezone-aware UTC datetime.
    """
    offset = utc_offset(country)
    hour = draw_local_hour(rng, local_day.weekday())

    local_naive = datetime.combine(
        local_day,
        time(hour=hour, minute=rng.randrange(60), second=rng.randrange(60)),
    )
    # Attach the local offset, then normalise to UTC. Doing it in this order is
    # what makes the stored timestamp correspond to the intended local hour.
    local_aware = local_naive.replace(tzinfo=timezone(offset))
    utc_start = local_aware.astimezone(timezone.utc)

    if not_after is not None and utc_start > not_after:
        # Land somewhere in the final six hours before the ceiling instead of
        # clamping every overflow onto the identical instant, which would show up
        # as a spike at the right edge of every time series.
        slack_seconds = rng.randrange(6 * 3600)
        utc_start = not_after - timedelta(seconds=slack_seconds)

    return utc_start


def local_date_of(moment: datetime, country: str) -> date:
    """Return the local calendar date for a UTC instant.

    The generator needs this when deciding whether a session lands on a holiday:
    the answer must follow the user's calendar, not UTC's.

    Args:
        moment: A timezone-aware UTC datetime.
        country: The user's country name.

    Returns:
        The local date.
    """
    return (moment + utc_offset(country)).date()


def summarise_shape() -> dict[str, object]:
    """Return the module's key parameters for the data-quality report.

    Returns:
        A mapping describing the peak hours, weekday spread, holidays and growth,
        so the report can state what the generator was *told* to do alongside
        what the data actually shows.
    """
    peak_weekday = max(range(24), key=lambda h: WEEKDAY_HOUR_WEIGHTS[h])
    peak_weekend = max(range(24), key=lambda h: WEEKEND_HOUR_WEIGHTS[h])
    return {
        "peak_hour_weekday_local": peak_weekday,
        "peak_hour_weekend_local": peak_weekend,
        "busiest_weekday": max(range(7), key=lambda d: WEEKDAY_MULTIPLIER[d]),
        "quietest_weekday": min(range(7), key=lambda d: WEEKDAY_MULTIPLIER[d]),
        "weekend_lift": round(
            (WEEKDAY_MULTIPLIER[5] + WEEKDAY_MULTIPLIER[6]) / 2
            / (sum(WEEKDAY_MULTIPLIER[:5]) / 5),
            3,
        ),
        "holidays": [
            {
                "name": holiday.name,
                "multiplier": holiday.multiplier,
                "scope": "global" if holiday.countries is None else sorted(holiday.countries),
            }
            for holiday in HOLIDAYS
        ],
        "monthly_growth_rate": MONTHLY_GROWTH_RATE,
        "timezone_note": (
            "Timestamps are generated in each user's local time and stored as UTC. "
            "Hour-of-day analysis in UTC therefore shows a smeared peak, which is "
            "correct: the world does not watch television simultaneously."
        ),
    }


__all__ = [
    "COUNTRY_UTC_OFFSET_HOURS",
    "DAILY_NOISE_SIGMA",
    "HOLIDAYS",
    "MONTHLY_GROWTH_RATE",
    "WEEKDAY_HOUR_WEIGHTS",
    "WEEKDAY_MULTIPLIER",
    "WEEKEND_HOUR_WEIGHTS",
    "Holiday",
    "active_holidays",
    "day_intensity",
    "draw_local_hour",
    "draw_session_start",
    "growth_multiplier",
    "holiday_multiplier",
    "hour_weights_for",
    "local_date_of",
    "summarise_shape",
    "utc_offset",
    "weekday_multiplier",
]
