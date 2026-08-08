"""Response models for the five funnel queries.

Two funnels — session-scoped discovery-to-watch, user-scoped signup-to-subscribe — plus
step drop-off, elapsed time between steps, and a segmented view. Semantics live in
:mod:`app.repositories.funnel`.

``pct_of_previous`` and ``dropped_from_previous`` are null on the first step of both
funnels, and that is the correct value rather than a gap: there is no previous step to
compare against. A zero there would read as "nobody dropped", which is a measurement,
and the first row of a funnel has not measured anything of the kind.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import Number, RowModel


class DiscoveryFunnelRow(RowModel):
    """One step of the session-scoped discovery funnel.

    Counted in sessions, not users: the same person browsing on two evenings appears
    twice, which is what a per-visit funnel should measure.

    Attributes:
        step_order: Position in the funnel, ascending.
        step_name: Human-readable step name.
        sessions: Sessions reaching this step.
        pct_of_entry: Sessions here as a share of the funnel's first step.
        pct_of_previous: Share of the immediately preceding step, or ``None`` on the
            first step.
        dropped_from_previous: Sessions lost since the preceding step, or ``None`` on
            the first step.
    """

    step_order: int
    step_name: str
    sessions: int
    pct_of_entry: Number
    pct_of_previous: Number | None = Field(
        default=None,
        description="Null on the first step: nothing precedes it.",
    )
    dropped_from_previous: int | None = None


class SubscribeFunnelRow(RowModel):
    """One step of the user-scoped signup-to-subscribe funnel.

    Counted in users, so each person appears at most once per step.

    Attributes:
        step_order: Position in the funnel, ascending.
        step_name: Human-readable step name.
        users: Users reaching this step.
        pct_of_signups: Users here as a share of everyone who signed up.
        pct_of_previous: Share of the immediately preceding step, or ``None`` on the
            first step.
        dropped_from_previous: Users lost since the preceding step, or ``None`` on the
            first step.
    """

    step_order: int
    step_name: str
    users: int
    pct_of_signups: Number
    pct_of_previous: Number | None = Field(
        default=None,
        description="Null on the first step: nothing precedes it.",
    )
    dropped_from_previous: int | None = None


class StepDropoffRow(RowModel):
    """One step-to-step transition, ranked by how much it costs.

    Attributes:
        step_order: Position of the transition.
        from_step: Step being left.
        to_step: Step being entered.
        from_count: Population at ``from_step``.
        to_count: Population at ``to_step``.
        users_lost: The difference.
        dropoff_pct: Share lost at this transition.
        conversion_pct: Share retained, the complement of ``dropoff_pct``.
        loss_rank: Rank by absolute loss, ``1`` being the largest. The step to fix
            first by volume.
        rate_rank: Rank by loss *rate*, ``1`` being the leakiest. Differs from
            ``loss_rank`` whenever a late step leaks badly on a small population —
            reading only one of the two hides one of the two problems.
    """

    step_order: int
    from_step: str
    to_step: str
    from_count: int
    to_count: int
    users_lost: int
    dropoff_pct: Number | None = None
    conversion_pct: Number | None = None
    loss_rank: int
    rate_rank: int


class TimeBetweenStepsRow(RowModel):
    """How long each funnel transition takes.

    Percentiles rather than a mean: the distribution has a long tail — a user who
    resumes a session the next evening contributes hours — and a mean would describe
    nobody.

    Attributes:
        transition: Human-readable transition name.
        step_order: Position of the transition.
        observations: Transitions observed. Negative and null gaps are excluded by the
            query, so this can be smaller than the step counts in the funnel itself.
        p25_seconds: 25th percentile elapsed time.
        median_seconds: Median elapsed time.
        p90_seconds: 90th percentile elapsed time.
        median_minutes: The median again in minutes, for a friendlier axis.
    """

    step_order: int
    transition: str
    observations: int
    p25_seconds: Number | None = None
    median_seconds: Number | None = None
    p90_seconds: Number | None = None
    median_minutes: Number | None = None


class FunnelBySegmentRow(RowModel):
    """The discovery funnel collapsed to one row per segment.

    Attributes:
        segment: The dimension value. Which dimensions are available is set by
            ``segment_by``, and the allowlist differs from retention's — the two
            queries expose different dimensions on purpose.
        opened: Sessions that opened the app.
        discovered: Sessions that reached discovery.
        viewed: Sessions that viewed a title's detail page.
        started: Sessions that started playback.
        completed: Sessions that completed something.
        open_to_view_pct: Conversion from ``opened`` to ``viewed``.
        view_to_start_pct: Conversion from ``viewed`` to ``started``.
        start_to_complete_pct: Conversion from ``started`` to ``completed``.
        end_to_end_pct: Conversion from ``opened`` to ``completed``. Not the product
            of the three stage rates, because each is computed on its own
            denominator.
    """

    segment: str
    opened: int
    discovered: int
    viewed: int
    started: int
    completed: int
    open_to_view_pct: Number | None = None
    view_to_start_pct: Number | None = None
    start_to_complete_pct: Number | None = None
    end_to_end_pct: Number | None = Field(
        default=None,
        description="Not the product of the stage rates; each has its own denominator.",
    )


__all__ = [
    "DiscoveryFunnelRow",
    "FunnelBySegmentRow",
    "StepDropoffRow",
    "SubscribeFunnelRow",
    "TimeBetweenStepsRow",
]
