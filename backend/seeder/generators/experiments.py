"""A/B experiment definitions, assignment, and application of a true effect.

The honest bit
--------------
Each spec in :data:`~seeder.config.EXPERIMENT_SPECS` carries a ``true_lift`` — the
effect the generator actually applies to the treatment arm. Two of the eight are
declared ``0.0``, and one is negative.

That matters because the significance testing in ``app/services/stats.py`` is then
answering a question with a known correct answer. The null experiments should fail
to reach significance; the 8.4% autoplay lift should clear it comfortably; the
4.1% paywall lift should sit near the boundary. An experiments page where every
test is a winner is a page nobody with A/B testing experience believes, and it
invites exactly the question you do not want: "did you just make these numbers up?"

Assignment
----------
Users are assigned by hashing ``(experiment_key, user_id)``, not by a random draw.
This is how real assignment services work, and it buys two properties: assignment
is stable if the seeder is re-run with a different profile, and the bucketing is
independent between experiments, so a user in treatment for one test is not
correlated with their arm in another. A naive ``rng.random()`` per user per
experiment would give neither.

Eligibility is signup-based: a user must have signed up before the experiment
started, because you cannot enrol someone who does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from typing import TYPE_CHECKING, Final

from seeder import config

if TYPE_CHECKING:
    from random import Random

    from seeder.generators.users import UserSpec

#: Metrics whose lift is applied during session and event generation, mapped to
#: the behavioural quantity the treatment arm modifies.
#:
#: A metric absent from this mapping is still recorded and still analysed, but the
#: generator applies no effect to it — which is the correct handling for a metric
#: the simulation has no mechanism for.
METRIC_LEVERS: Final[dict[str, str]] = {
    "trailer_to_start": "playback_probability",
    "subscription_conversion": "conversion_log_odds",
    "completion_rate": "completion_rate",
    "sessions_per_user": "session_frequency",
    "day7_retention": "early_retention",
    "session_duration": "session_minutes",
}


@dataclass(slots=True)
class ExperimentDefinition:
    """One ``core.experiments`` row plus the generator-side effect.

    Attributes:
        experiment_id: Surrogate key, assigned sequentially from 1.
        key: URL slug, matching ``ck_experiments_key_slug``.
        name: Human-readable title.
        hypothesis: What the team believed before running it.
        primary_metric: The metric the test is judged on.
        variants: Ordered variant names, control first.
        traffic_allocation: Fraction of eligible users enrolled.
        started_on: First day of the run.
        ended_on: Last day, or ``None`` while running.
        status: A ``core.exp_status`` label.
        true_lift: Relative effect applied to non-control arms. Zero for a null
            experiment; negative for a regression.
    """

    experiment_id: int
    key: str
    name: str
    hypothesis: str
    primary_metric: str
    variants: tuple[str, ...]
    traffic_allocation: float
    started_on: date
    ended_on: date | None
    status: str
    true_lift: float

    def as_row(self) -> tuple[object, ...]:
        """Render as a tuple for binary ``COPY``.

        Returns:
            Values in :data:`seeder.loaders.EXPERIMENT_COLUMNS` order. ``variants``
            is JSON-encoded for the ``JSONB`` column.
        """
        return (
            self.experiment_id,
            self.key,
            self.name,
            self.hypothesis,
            self.primary_metric,
            json.dumps(list(self.variants)),
            round(self.traffic_allocation, 2),
            self.started_on,
            self.ended_on,
            self.status,
        )

    def covers(self, day: date) -> bool:
        """Return whether the experiment was live on a date.

        Args:
            day: The date to test.

        Returns:
            True when ``day`` falls within the run.
        """
        if day < self.started_on:
            return False
        return self.ended_on is None or day <= self.ended_on


@dataclass(slots=True)
class Assignment:
    """One ``core.experiment_assignments`` row.

    Attributes:
        experiment_id: The experiment.
        user_id: The enrolled user.
        variant: Which arm they saw.
        assigned_at: Enrolment timestamp, in UTC.
    """

    experiment_id: int
    user_id: int
    variant: str
    assigned_at: datetime

    def as_row(self) -> tuple[object, ...]:
        """Render as a tuple for binary ``COPY``.

        Returns:
            Values in :data:`seeder.loaders.ASSIGNMENT_COLUMNS` order.
        """
        return (self.experiment_id, self.user_id, self.variant, self.assigned_at)


def _bucket(key: str, user_id: int, buckets: int) -> int:
    """Hash a user into one of ``buckets`` for an experiment.

    BLAKE2b over ``key:user_id``. Deterministic, uniform, and independent between
    experiments because the key is part of the input — the same property a real
    assignment service relies on.

    Args:
        key: Experiment slug.
        user_id: The user.
        buckets: Number of buckets.

    Returns:
        A bucket index in ``[0, buckets)``.
    """
    digest = hashlib.blake2b(f"{key}:{user_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % buckets


def build_experiments(
    rng: Random,
    *,
    count: int,
    window_start: date,
    window_end: date,
) -> list[ExperimentDefinition]:
    """Create experiment definitions spread across the window.

    Runs are staggered rather than simultaneous, which is what a real experiment
    calendar looks like and lets the Experiments page show a mix of completed and
    running tests.

    Args:
        rng: Seeded random source.
        count: How many of the available specs to use.
        window_start: First day of the simulation window.
        window_end: Last day of the simulation window.

    Returns:
        Definitions ordered by start date.
    """
    specs = config.EXPERIMENT_SPECS[: min(count, len(config.EXPERIMENT_SPECS))]
    window_days = max((window_end - window_start).days, 30)

    definitions: list[ExperimentDefinition] = []

    for index, (
        key,
        name,
        hypothesis,
        metric,
        variants,
        allocation,
        true_lift,
    ) in enumerate(specs):
        low, high = config.EXPERIMENT_DURATION_FRACTION
        duration = max(14, int(window_days * rng.uniform(low, high)))

        # Stagger starts across the window, leaving the first eighth clear so
        # there is a pre-experiment baseline in the data.
        earliest = int(window_days * 0.12)
        latest = max(earliest + 1, window_days - duration)
        start_offset = earliest + int(
            (latest - earliest) * (index / max(len(specs) - 1, 1)) * rng.uniform(0.75, 1.0)
        )
        started_on = window_start + timedelta(days=min(start_offset, window_days - 15))
        planned_end = started_on + timedelta(days=duration)

        # An experiment whose planned end falls beyond the window is still
        # running, which is the realistic state for the most recent tests.
        if planned_end >= window_end:
            ended_on = None
            status = "running"
        else:
            ended_on = planned_end
            # A minority of completed experiments were stopped early. Worth having:
            # it gives the Experiments page a status a reader has to think about.
            status = "stopped" if rng.random() < 0.18 else "completed"

        definitions.append(
            ExperimentDefinition(
                experiment_id=index + 1,
                key=key,
                name=name,
                hypothesis=hypothesis,
                primary_metric=metric,
                variants=variants,
                traffic_allocation=allocation,
                started_on=started_on,
                ended_on=ended_on,
                status=status,
                true_lift=true_lift,
            )
        )

    definitions.sort(key=lambda definition: definition.started_on)
    for position, definition in enumerate(definitions, start=1):
        definition.experiment_id = position

    return definitions


def assign_users(
    definitions: list[ExperimentDefinition],
    users: list[UserSpec],
) -> tuple[list[Assignment], dict[int, dict[int, str]]]:
    """Enrol eligible users into every experiment.

    Args:
        definitions: The experiment definitions.
        users: The generated population.

    Returns:
        ``(assignments, lookup)`` where ``lookup`` maps ``user_id`` to
        ``{experiment_id: variant}`` for O(1) access during the timeline walk.
    """
    assignments: list[Assignment] = []
    lookup: dict[int, dict[int, str]] = {}

    #: Bucket resolution. 1000 gives allocation precision to 0.1%, which is finer
    #: than any allocation in the specs.
    buckets = 1000

    for definition in definitions:
        # Enrolment happens on the experiment's start date, at a fixed time of
        # day, in UTC. A real service would stagger this; a fixed instant keeps
        # `assigned_at` deterministic and satisfies ck_assignments_no_future.
        assigned_at = datetime.combine(
            definition.started_on, time(hour=9, minute=0), tzinfo=timezone.utc
        )

        allocation_cutoff = int(definition.traffic_allocation * buckets)
        arm_count = len(definition.variants)

        for user in users:
            # Cannot enrol a user who has not signed up yet.
            if user.signup_date > definition.started_on:
                continue

            bucket = _bucket(definition.key, user.user_id, buckets)
            if bucket >= allocation_cutoff:
                continue

            # Second, independent hash for arm selection. Reusing the first bucket
            # would correlate arm with allocation position and skew the arms
            # whenever allocation is not a clean multiple of the arm count.
            arm = _bucket(f"{definition.key}:arm", user.user_id, arm_count)
            variant = definition.variants[arm]

            assignments.append(
                Assignment(
                    experiment_id=definition.experiment_id,
                    user_id=user.user_id,
                    variant=variant,
                    assigned_at=assigned_at,
                )
            )
            lookup.setdefault(user.user_id, {})[definition.experiment_id] = variant

    return assignments, lookup


class EffectResolver:
    """Resolves the active experiment effect for a user on a given day.

    Consulted by the session and event generators to apply ``true_lift``. Kept as a
    small class rather than a free function so the definition list and the
    assignment lookup are bound once instead of threaded through every call in the
    hot loop.
    """

    __slots__ = ("_by_metric", "_lookup")

    def __init__(
        self,
        definitions: list[ExperimentDefinition],
        lookup: dict[int, dict[int, str]],
    ) -> None:
        """Initialise the resolver.

        Args:
            definitions: Every experiment definition.
            lookup: ``user_id -> {experiment_id: variant}`` from
                :func:`assign_users`.
        """
        self._lookup = lookup
        self._by_metric: dict[str, list[ExperimentDefinition]] = {}
        for definition in definitions:
            self._by_metric.setdefault(definition.primary_metric, []).append(definition)

    def multiplier(self, user_id: int, metric: str, day: date) -> float:
        """Return the multiplicative effect on a metric for this user today.

        Args:
            user_id: The user.
            metric: One of the keys of :data:`METRIC_LEVERS`.
            day: The simulation date.

        Returns:
            ``1.0`` when the user is in control, not enrolled, or the experiment
            is not live; otherwise ``1 + true_lift``.
        """
        enrolments = self._lookup.get(user_id)
        if not enrolments:
            return 1.0

        multiplier = 1.0
        for definition in self._by_metric.get(metric, ()):
            variant = enrolments.get(definition.experiment_id)
            if variant is None or variant == "control":
                continue
            if not definition.covers(day):
                continue

            lift = definition.true_lift
            # A three-arm test splits its effect: variant_b gets a smaller dose
            # than variant_a, which is what a real ramped rollout looks like.
            if len(definition.variants) > 2:
                arm_index = definition.variants.index(variant)
                lift *= 1.0 if arm_index == 1 else 0.6

            multiplier *= 1.0 + lift

        return multiplier

    def additive(self, user_id: int, metric: str, day: date) -> float:
        """Return an additive effect, for quantities measured in log-odds.

        Conversion is modelled in log-odds, so a relative lift must be converted
        before it can be applied. Multiplying log-odds directly would make the
        effect size depend on the user's baseline, which is not what a lift means.

        Args:
            user_id: The user.
            metric: One of the keys of :data:`METRIC_LEVERS`.
            day: The simulation date.

        Returns:
            The log-odds shift; ``0.0`` when no effect applies.
        """
        import math

        multiplier = self.multiplier(user_id, metric, day)
        if multiplier == 1.0:
            return 0.0
        # log of the odds ratio implied by the relative lift. For small lifts this
        # is close to the lift itself, which keeps the declared true_lift
        # interpretable in the report.
        return math.log(max(multiplier, 1e-6))


__all__ = [
    "METRIC_LEVERS",
    "Assignment",
    "EffectResolver",
    "ExperimentDefinition",
    "assign_users",
    "build_experiments",
]
