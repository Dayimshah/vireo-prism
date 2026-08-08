"""Response models for the six retention queries.

Three definitions of retention, two segmentations and a resurrection series. The
definitions are not interchangeable and are not nested inside one another — see
:mod:`app.repositories.retention`, which documents why classic, rolling and unbounded
retention give three different numbers for the same cohort and day, and why only one of
them is monotonic.

The three ``*_pct`` models below are field-identical on purpose. They are declared
separately rather than aliased to one class so that OpenAPI names the response of each
endpoint after the definition it used: a reader looking at ``/retention/rolling`` sees
``RetentionRollingRow`` and has a term to search for. Collapsing them would save
twenty lines and lose the one thing most likely to be misread.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolves annotations at runtime

from pydantic import Field

from app.schemas.base import Number, RowModel


class RetentionNdayRow(RowModel):
    """Classic N-day retention: active *on* day N exactly.

    Attributes:
        day_n: Days since signup.
        cohort_size: Users eligible to be observed at day N.
        retained_users: Users active on that exact day.
        retention_pct: ``retained_users / cohort_size`` as a percentage.
    """

    day_n: int
    cohort_size: int
    retained_users: int
    retention_pct: Number


class RetentionRollingRow(RowModel):
    """Rolling retention: active on day N *or any day after*.

    Looks forward, so it is always at least as large as the classic figure and is not
    comparable with the unbounded one.

    Attributes:
        day_n: Days since signup.
        cohort_size: Users eligible to be observed at day N.
        retained_users: Users active on day N or later.
        retention_pct: ``retained_users / cohort_size`` as a percentage.
    """

    day_n: int
    cohort_size: int
    retained_users: int
    retention_pct: Number


class RetentionUnboundedRow(RowModel):
    """Unbounded retention: active at any point *within* the first N days.

    Looks backward. Not monotonic across ``day_n``, because ``cohort_size`` shrinks as
    the eligibility rule tightens — percentages at different ``day_n`` describe
    different populations, which :mod:`app.repositories.retention` explains at length.

    Attributes:
        day_n: Days since signup.
        cohort_size: Users eligible to be observed over the first N days.
        retained_users: Users active at least once in that span.
        retention_pct: ``retained_users / cohort_size`` as a percentage.
    """

    day_n: int
    cohort_size: int
    retained_users: int
    retention_pct: Number


class RetentionBySegmentRow(RowModel):
    """N-day retention split by one dimension.

    Attributes:
        segment: The dimension value, e.g. a country name. Which dimensions are
            available is set by ``segment_by`` and differs from the funnel's
            allowlist — the two queries expose different dimensions, which is
            deliberate rather than an oversight.
        day_n: Days since signup.
        cohort_size: Users in this segment eligible at day N.
        retained_users: Retained users in this segment.
        retention_pct: Retention within the segment, not a share of the whole.
    """

    segment: str
    day_n: int
    cohort_size: int
    retained_users: int
    retention_pct: Number


class RetentionCurveByPersonaRow(RowModel):
    """Weekly retention curve for each persona.

    Attributes:
        persona: Persona name.
        week_n: Weeks since signup; ``0`` is the signup week itself.
        cohort_size: Users in the persona eligible at week N.
        retained_users: Retained users in the persona.
        retention_pct: Retention within the persona.
    """

    persona: str
    week_n: int
    cohort_size: int
    retained_users: int
    retention_pct: Number


class ResurrectionRateRow(RowModel):
    """Monthly resurrection: dormant users who came back.

    Attributes:
        month: First day of the calendar month.
        resurrected: Dormant users who became active this month.
        dormant_pool: Users who were dormant entering the month, the denominator.
        active_returning: Active users this month who had been seen before.
        avg_dormant_days: Mean days those users had been dormant, or ``None`` when
            nobody resurrected — there is no average of an empty set.
        resurrection_rate_pct: ``resurrected / dormant_pool`` as a percentage, or
            ``None`` when the pool is empty.
    """

    month: date
    resurrected: int
    dormant_pool: int
    active_returning: int
    avg_dormant_days: Number | None = Field(
        default=None,
        description="Mean dormancy before returning. Null when nobody resurrected.",
    )
    resurrection_rate_pct: Number | None = Field(
        default=None,
        description="Null when the dormant pool was empty, which is undefined, not zero.",
    )


__all__ = [
    "ResurrectionRateRow",
    "RetentionBySegmentRow",
    "RetentionCurveByPersonaRow",
    "RetentionNdayRow",
    "RetentionRollingRow",
    "RetentionUnboundedRow",
]
