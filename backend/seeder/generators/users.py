"""Generate the user population.

Produces one :class:`UserSpec` per account: the row destined for ``core.users``
plus the behavioural profile and derived facts the rest of the simulation needs.

Two things here are more than a weighted draw.

**Signup dates follow a growth curve.** ``SIGNUP_MONTH_WEIGHTS`` rises across the
window with a December pullback, so the cohort sizes on the retention page differ
for a reason, and the "new users" line on the executive dashboard trends upward.

**Persona is conditional on channel.** ``CHANNEL_PERSONA_AFFINITY`` skews the
persona draw by acquisition source, which is the mechanism behind the whole
marketing story: Referral genuinely brings more Binge Watchers and Premium
Loyalists, so it genuinely retains and monetises better. The alternative — drawing
persona independently and then applying a channel coefficient at conversion time —
would produce the same headline number with no causal chain underneath it, and it
would fall apart the moment someone cross-tabulated persona by channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from seeder import config
from seeder.personas import UserProfile, build_profile

if TYPE_CHECKING:
    from random import Random


@dataclass(slots=True)
class UserSpec:
    """One generated user, with everything the timeline walk needs.

    Attributes:
        user_id: Surrogate key, assigned sequentially from 1.
        signup_date: Account creation date.
        country_id: Foreign key into ``core.countries``.
        country_name: Denormalised for timezone and holiday lookups.
        country_tier: Monetisation band, 1-3. Feeds the conversion model.
        device_id: Signup device.
        channel_id: Acquisition channel.
        channel_name: Denormalised for the conversion coefficient lookup.
        persona_id: Persona recorded on the row. For a graduated New Explorer this
            is the destination persona.
        gender: One of the four ``ck_users_gender`` values.
        age: Age in years.
        app_version: Semantic version string.
        profile: Realised behavioural parameters.
        observation_days: Days between signup and the window end.
        is_premium: Current paid state. Mutated by the subscription generator.
        last_seen_at: Most recent event time. Mutated by the timeline walk.
        churned_at: Churn date, or ``None``. Mutated by the timeline walk.
    """

    user_id: int
    signup_date: date
    country_id: int
    country_name: str
    country_tier: int
    device_id: int
    channel_id: int
    channel_name: str
    persona_id: int
    gender: str
    age: int
    app_version: str
    profile: UserProfile
    observation_days: int
    is_premium: bool = False
    last_seen_at: object | None = None
    churned_at: date | None = None


def _weighted(rng: Random, weights: dict[str, float]) -> str:
    """Draw one key from a weight mapping.

    Args:
        rng: Seeded random source.
        weights: Value to relative weight.

    Returns:
        The selected key.
    """
    keys = list(weights)
    return rng.choices(keys, weights=[weights[key] for key in keys], k=1)[0]


def _draw_signup_date(
    rng: Random,
    window_start: date,
    window_end: date,
) -> date:
    """Draw a signup date following the growth curve.

    A month is chosen from :data:`~seeder.config.SIGNUP_MONTH_WEIGHTS`, then a day
    uniformly within it. The final ``MIN_OBSERVATION_DAYS`` are excluded so no
    cohort enters the dataset with too little history to appear honestly in a
    7-day retention chart.

    Args:
        rng: Seeded random source.
        window_start: First day of the simulation window.
        window_end: Last day of the simulation window.

    Returns:
        The signup date.
    """
    latest = window_end - timedelta(days=config.MIN_OBSERVATION_DAYS)
    span_days = max((latest - window_start).days, 1)

    weights = config.SIGNUP_MONTH_WEIGHTS
    # A window shorter than the weight table uses its leading entries; a longer
    # one repeats the tail, so the curve degrades gracefully at any WINDOW_MONTHS.
    month_count = max(1, int(span_days / 30.44))
    effective = [weights[min(i, len(weights) - 1)] for i in range(month_count)]

    month_index = rng.choices(range(month_count), weights=effective, k=1)[0]
    month_start = int(month_index * 30.44)
    month_end = min(int((month_index + 1) * 30.44), span_days)
    offset = rng.randrange(month_start, max(month_start + 1, month_end))

    return window_start + timedelta(days=min(offset, span_days))


def _draw_persona(rng: Random, channel_name: str) -> str:
    """Draw a persona, skewed by acquisition channel.

    Args:
        rng: Seeded random source.
        channel_name: The user's acquisition channel.

    Returns:
        A persona name.
    """
    affinity = config.CHANNEL_PERSONA_AFFINITY.get(channel_name, {})
    weighted = {
        persona: weight * affinity.get(persona, 1.0)
        for persona, weight in config.PERSONA_WEIGHTS.items()
    }
    return _weighted(rng, weighted)


def _draw_age(rng: Random, persona: str) -> int:
    """Draw an age, skewed by persona.

    Args:
        rng: Seeded random source.
        persona: The user's persona name.

    Returns:
        An age within the ``ck_users_age_range`` bounds.
    """
    skew = config.PERSONA_AGE_SKEW.get(persona, {})
    bands = config.AGE_BANDS
    weights = [weight * skew.get((low, high), 1.0) for low, high, weight in bands]
    low, high, _ = bands[rng.choices(range(len(bands)), weights=weights, k=1)[0]]
    return rng.randint(low, high)


def _draw_app_version(rng: Random, signup_date: date, window_end: date) -> str:
    """Draw an app version consistent with account age.

    Recent signups skew current; long-tenured accounts carry the stale tail. This
    gives the churn scorecard a legitimate secondary signal and makes the version
    adoption breakdown on the Users page non-trivial.

    Args:
        rng: Seeded random source.
        signup_date: The user's signup date.
        window_end: Last day of the simulation window.

    Returns:
        A semantic version string matching ``ck_users_app_version_semver``.
    """
    tenure_days = (window_end - signup_date).days
    versions = list(config.APP_VERSION_WEIGHTS)
    weights = list(config.APP_VERSION_WEIGHTS.values())

    if tenure_days > 300:
        # Older accounts drift onto older builds: boost the tail, damp the head.
        weights = [
            weight * (2.4 if index >= len(versions) - 3 else 0.7)
            for index, weight in enumerate(weights)
        ]

    return rng.choices(versions, weights=weights, k=1)[0]


def generate_users(
    rng: Random,
    *,
    count: int,
    window_start: date,
    window_end: date,
    country_ids: dict[str, int],
    country_tiers: dict[str, int],
    device_ids: dict[str, int],
    channel_ids: dict[str, int],
    persona_ids: dict[str, int],
    persona_bases: dict[str, tuple[float, float, float]],
    genre_names: tuple[str, ...],
) -> list[UserSpec]:
    """Generate the user population.

    Args:
        rng: Seeded random source.
        count: Number of users to generate.
        window_start: First day of the simulation window.
        window_end: Last day of the simulation window.
        country_ids: Country name to ``country_id``.
        country_tiers: Country name to monetisation tier.
        device_ids: Device name to ``device_id``.
        channel_ids: Channel name to ``channel_id``.
        persona_ids: Persona name to ``persona_id``.
        persona_bases: Persona name to
            ``(sessions_per_week, completion_rate, churn_propensity)`` read from
            ``core.personas``.
        genre_names: Every genre name, for personal taste assignment.

    Returns:
        User specs ordered by ``user_id``, which is also signup order.

    Raises:
        KeyError: If a distribution names a dimension row that does not exist,
            meaning ``seeder/config.py`` and Alembic revision 0002 have drifted.
    """
    for label, wanted, available in (
        ("country", set(config.COUNTRY_WEIGHTS), set(country_ids)),
        ("device", set(config.SIGNUP_DEVICE_WEIGHTS), set(device_ids)),
        ("channel", set(config.CHANNEL_WEIGHTS), set(channel_ids)),
        ("persona", set(config.PERSONA_WEIGHTS), set(persona_ids)),
    ):
        missing = sorted(wanted - available)
        if missing:
            raise KeyError(
                f"seeder/config.py references {label} rows absent from the database: "
                f"{', '.join(missing)}. config.py and Alembic revision 0002 have drifted."
            )

    specs: list[UserSpec] = []

    for user_id in range(1, count + 1):
        signup_date = _draw_signup_date(rng, window_start, window_end)
        observation_days = (window_end - signup_date).days

        country_name = _weighted(rng, config.COUNTRY_WEIGHTS)
        channel_name = _weighted(rng, config.CHANNEL_WEIGHTS)
        persona_name = _draw_persona(rng, channel_name)

        base_sessions, base_completion, base_churn = persona_bases[persona_name]
        profile = build_profile(
            rng,
            persona=persona_name,
            base_sessions_per_week=base_sessions,
            base_completion_rate=base_completion,
            base_churn_propensity=base_churn,
            all_genres=genre_names,
            observation_days=observation_days,
        )

        specs.append(
            UserSpec(
                user_id=user_id,
                signup_date=signup_date,
                country_id=country_ids[country_name],
                country_name=country_name,
                country_tier=country_tiers[country_name],
                device_id=device_ids[_weighted(rng, config.SIGNUP_DEVICE_WEIGHTS)],
                channel_id=channel_ids[channel_name],
                channel_name=channel_name,
                # The stored persona is the profile's *destination* persona, so a
                # graduated New Explorer appears as what they became.
                persona_id=persona_ids[profile.persona],
                gender=_weighted(rng, config.GENDER_WEIGHTS),
                age=_draw_age(rng, profile.persona),
                app_version=_draw_app_version(rng, signup_date, window_end),
                profile=profile,
                observation_days=observation_days,
            )
        )

    # Sorting by signup date makes user_id monotonic in signup time, which is what
    # a real auto-increment key would give and makes cohort queries read naturally.
    specs.sort(key=lambda spec: spec.signup_date)
    for index, spec in enumerate(specs, start=1):
        spec.user_id = index

    return specs


def user_row(spec: UserSpec) -> tuple[object, ...]:
    """Render a spec as a ``core.users`` row.

    Column order matches :data:`seeder.loaders.USER_COLUMNS`.

    Args:
        spec: The generated user.

    Returns:
        A tuple ready for binary ``COPY``.
    """
    return (
        spec.user_id,
        spec.signup_date,
        spec.country_id,
        spec.device_id,
        spec.channel_id,
        spec.persona_id,
        spec.is_premium,
        spec.gender,
        spec.age,
        spec.app_version,
        spec.last_seen_at,
        spec.churned_at,
    )


__all__ = ["UserSpec", "generate_users", "user_row"]
