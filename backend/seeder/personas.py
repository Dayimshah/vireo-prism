"""Per-user behaviour profiles derived from the eight personas.

Alembic revision 0002 stores three coefficients per persona because those three
are the ones the *analytics layer* needs in order to explain a chart. This module
holds everything else — genre affinity, search-versus-browse bias, device
preference, episode throughput, tenure trajectory — because no SQL query reads
them, only the generator does.

Within-persona variance is the point
------------------------------------
The important function here is :func:`build_profile`, and the important thing it
does is *perturb*. If every Binge Watcher had exactly 5.4 sessions per week, the
sessions-per-user histogram would show eight sharp spikes, one per persona, and
the dataset would be visibly artificial at a glance. Instead each user draws their
own rate from a log-normal centred on the persona base, so the population
histogram is smooth and continuous while the *group means* still differ exactly as
declared.

That distinction — smooth individuals, distinguishable groups — is what makes a
segmentation chart look like real data.

New Explorer graduates
----------------------
"Inside the first 30 days" is not a stable identity, so a New Explorer converts
into one of the other seven personas after :data:`GRADUATION_DAYS`. Their
``persona_id`` in ``core.users`` records where they *ended up*, which is what a
real product analyst would see: nobody is labelled "new" eighteen months in. The
early-life behaviour still reflects exploration, so day-1 and day-7 retention for
these users is genuinely different from their eventual steady state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from random import Random

# ===========================================================================
# Per-persona behavioural parameters
# ===========================================================================


@dataclass(frozen=True, slots=True)
class PersonaBehaviour:
    """Behavioural parameters for one persona.

    Attributes:
        genre_affinity: Multipliers on a genre's selection weight. Absent genres
            default to 1.0.
        content_type_affinity: Multipliers by ``core.content_type`` label.
        search_bias: Probability the user reaches content by searching rather than
            browsing. High for people who know what they want.
        titles_per_session: ``(min, mode, max)`` distinct titles engaged per
            session, triangular.
        playback_probability: Chance a session contains any playback at all. The
            complement is a browse-only session, which is a large and real share
            of streaming traffic.
        episodes_per_sitting: ``(min, mode, max)`` consecutive episodes for a
            series. The mechanical definition of bingeing.
        session_minutes: ``(min, mode, max)`` session length in minutes.
        watchlist_probability: Chance of adding a viewed title to the watchlist.
        subscribe_click_probability: Chance of tapping the paywall in a session,
            given the user is not already paying.
        popularity_bias: Exponent applied to a title's popularity when weighting
            selection. Above 1 concentrates on hits; below 1 spreads into the
            catalogue tail.
        recency_bias: Multiplier favouring recently added titles.
        activity_trend: Multiplicative change in session frequency per month of
            tenure. Below 1 is a user drifting away.
        rating_propensity: Multiplier on the base chance of rating a completion.
    """

    genre_affinity: dict[str, float]
    content_type_affinity: dict[str, float]
    search_bias: float
    titles_per_session: tuple[float, float, float]
    playback_probability: float
    episodes_per_sitting: tuple[float, float, float]
    session_minutes: tuple[float, float, float]
    watchlist_probability: float
    subscribe_click_probability: float
    popularity_bias: float
    recency_bias: float
    activity_trend: float
    rating_propensity: float


#: Behaviour per persona. Keys must match ``core.personas.name``;
#: :func:`build_profile` raises on an unknown name so a rename fails loudly.
PERSONA_BEHAVIOUR: Final[dict[str, PersonaBehaviour]] = {
    "Binge Watcher": PersonaBehaviour(
        genre_affinity={
            "Crime": 1.7,
            "Thriller": 1.6,
            "Drama": 1.5,
            "Mystery": 1.5,
            "Fantasy": 1.3,
            "Sci-Fi": 1.2,
            "Stand-Up": 0.5,
            "Kids & Family": 0.3,
        },
        # Series-first by definition: you cannot binge a 110-minute film.
        content_type_affinity={"series": 2.4, "movie": 0.5, "documentary": 0.7, "stand_up": 0.4},
        search_bias=0.34,
        titles_per_session=(1.0, 1.4, 3.0),
        playback_probability=0.94,
        episodes_per_sitting=(1.0, 3.2, 8.0),
        session_minutes=(35.0, 145.0, 330.0),
        watchlist_probability=0.19,
        subscribe_click_probability=0.11,
        popularity_bias=0.85,  # will go deep into the catalogue for a good series
        recency_bias=1.25,
        activity_trend=0.995,  # essentially stable
        rating_propensity=1.15,
    ),
    "Movie Lover": PersonaBehaviour(
        genre_affinity={
            "Drama": 1.8,
            "Thriller": 1.5,
            "Crime": 1.3,
            "Sci-Fi": 1.4,
            "Romance": 1.2,
            "Documentary": 1.2,
            "Reality": 0.2,
            "Kids & Family": 0.4,
        },
        content_type_affinity={"movie": 2.8, "series": 0.3, "documentary": 1.4, "stand_up": 0.9},
        search_bias=0.52,  # arrives with a title in mind
        titles_per_session=(1.0, 1.1, 2.0),
        playback_probability=0.88,
        episodes_per_sitting=(1.0, 1.0, 2.0),
        session_minutes=(40.0, 118.0, 210.0),
        watchlist_probability=0.28,  # heavy watchlist curator
        subscribe_click_probability=0.13,
        popularity_bias=0.72,  # actively seeks the tail
        recency_bias=1.05,
        activity_trend=0.998,
        rating_propensity=1.55,  # opinionated
    ),
    "Anime Fan": PersonaBehaviour(
        genre_affinity={
            "Anime": 9.5,  # near-exclusive; the strongest affinity in the dataset
            "Fantasy": 1.4,
            "Action": 1.2,
            "Sci-Fi": 1.1,
            "Reality": 0.1,
            "Documentary": 0.2,
            "Stand-Up": 0.2,
        },
        content_type_affinity={"series": 3.2, "movie": 0.6, "documentary": 0.2, "stand_up": 0.2},
        search_bias=0.61,  # knows exactly which series and which season
        titles_per_session=(1.0, 1.3, 3.0),
        playback_probability=0.92,
        episodes_per_sitting=(1.0, 4.1, 11.0),  # 24-minute episodes stack up
        session_minutes=(30.0, 112.0, 280.0),
        watchlist_probability=0.34,  # highest watchlist use
        subscribe_click_probability=0.14,
        popularity_bias=0.65,  # deep-catalogue explorer within one genre
        recency_bias=1.75,  # simulcast behaviour: new episodes matter
        activity_trend=0.991,
        rating_propensity=1.42,
    ),
    "Sports Fan": PersonaBehaviour(
        genre_affinity={
            "Sports": 7.8,
            "Documentary": 1.9,
            "Action": 1.2,
            "Romance": 0.2,
            "Anime": 0.2,
            "Kids & Family": 0.4,
        },
        content_type_affinity={"documentary": 2.6, "series": 1.3, "movie": 0.7, "stand_up": 0.6},
        search_bias=0.44,
        titles_per_session=(1.0, 1.2, 2.0),
        playback_probability=0.85,
        episodes_per_sitting=(1.0, 1.6, 4.0),
        # Bursty: a fixture is watched end to end, and the gaps between are empty.
        session_minutes=(25.0, 96.0, 260.0),
        watchlist_probability=0.12,
        subscribe_click_probability=0.16,
        popularity_bias=1.35,  # follows the big events
        recency_bias=2.10,  # sport is worthless stale
        activity_trend=0.986,
        rating_propensity=0.72,
    ),
    "Casual Viewer": PersonaBehaviour(
        genre_affinity={
            "Comedy": 1.6,
            "Reality": 1.8,
            "Romance": 1.3,
            "Kids & Family": 1.4,
            "Documentary": 0.8,
            "Horror": 0.6,
            "Anime": 0.4,
        },
        content_type_affinity={"series": 1.2, "movie": 1.1, "documentary": 0.8, "stand_up": 1.3},
        search_bias=0.18,  # browses the rails, rarely searches
        titles_per_session=(1.0, 2.1, 5.0),  # lots of looking
        playback_probability=0.68,  # a third of sessions are browse-only
        episodes_per_sitting=(1.0, 1.2, 3.0),
        session_minutes=(12.0, 46.0, 140.0),
        watchlist_probability=0.08,
        subscribe_click_probability=0.07,
        popularity_bias=1.85,  # only watches what the homepage promotes
        recency_bias=1.15,
        activity_trend=0.982,
        rating_propensity=0.55,
    ),
    "Premium Loyalist": PersonaBehaviour(
        genre_affinity={
            "Drama": 1.5,
            "Documentary": 1.6,
            "Crime": 1.3,
            "Mystery": 1.3,
            "Thriller": 1.2,
            "Reality": 0.6,
        },
        content_type_affinity={"series": 1.6, "movie": 1.4, "documentary": 1.5, "stand_up": 1.1},
        search_bias=0.41,
        titles_per_session=(1.0, 1.5, 3.0),
        playback_probability=0.91,
        episodes_per_sitting=(1.0, 2.4, 6.0),
        session_minutes=(30.0, 108.0, 250.0),
        watchlist_probability=0.24,
        subscribe_click_probability=0.04,  # already paying; rarely sees a paywall
        popularity_bias=0.92,
        recency_bias=1.40,  # follows new releases closely
        activity_trend=1.002,  # the only persona that trends slightly up
        rating_propensity=1.28,
    ),
    "Churn Risk": PersonaBehaviour(
        genre_affinity={
            "Reality": 1.3,
            "Comedy": 1.2,
            "Horror": 1.1,
            "Documentary": 0.7,
            "Anime": 0.5,
        },
        content_type_affinity={"movie": 1.3, "series": 0.7, "documentary": 0.6, "stand_up": 1.2},
        search_bias=0.22,
        titles_per_session=(1.0, 1.6, 4.0),
        playback_probability=0.51,  # half the sessions never start anything
        episodes_per_sitting=(1.0, 1.0, 2.0),
        session_minutes=(6.0, 22.0, 75.0),  # shortest sessions in the dataset
        watchlist_probability=0.05,
        subscribe_click_probability=0.05,
        popularity_bias=1.95,
        recency_bias=1.05,
        # The defining property: activity decays 6% per month, which is what makes
        # this cohort detectable by the churn scorecard before they actually leave.
        activity_trend=0.938,
        rating_propensity=0.34,
    ),
    "New Explorer": PersonaBehaviour(
        genre_affinity={
            # Deliberately flat: a new user has not revealed a preference yet, and
            # imposing one would leak the future into their early behaviour.
            "Action": 1.1,
            "Comedy": 1.1,
            "Drama": 1.1,
        },
        content_type_affinity={"series": 1.1, "movie": 1.2, "documentary": 0.9, "stand_up": 1.0},
        search_bias=0.47,  # searching a lot, because nothing is familiar
        titles_per_session=(1.0, 2.6, 6.0),  # widest sampling of any persona
        playback_probability=0.74,
        episodes_per_sitting=(1.0, 1.4, 3.0),
        session_minutes=(15.0, 58.0, 165.0),
        watchlist_probability=0.21,  # stockpiling for later
        subscribe_click_probability=0.12,
        popularity_bias=1.55,  # starts with what is promoted
        recency_bias=1.30,
        activity_trend=0.965,  # novelty fades before graduation
        rating_propensity=0.88,
    ),
}

#: Days after signup at which a New Explorer becomes one of the other personas.
GRADUATION_DAYS: Final[int] = 30

#: Where New Explorers end up. Sums to 1.0. Weighted toward Casual Viewer, which
#: is the honest outcome: most new users of anything become light users.
GRADUATION_TARGETS: Final[dict[str, float]] = {
    "Casual Viewer": 0.37,
    "Movie Lover": 0.17,
    "Churn Risk": 0.16,
    "Binge Watcher": 0.12,
    "Anime Fan": 0.08,
    "Sports Fan": 0.06,
    "Premium Loyalist": 0.04,
}

#: Log-normal sigma for per-user perturbation of session frequency. 0.42 gives a
#: roughly 1.5x spread between the 25th and 75th percentile within one persona,
#: which is what turns eight spikes into one smooth histogram.
FREQUENCY_SIGMA: Final[float] = 0.42

#: Beta concentration for per-user completion rate. Higher values keep individuals
#: closer to their persona mean; 18 gives visible spread without letting a Binge
#: Watcher complete less than a Churn Risk.
COMPLETION_CONCENTRATION: Final[float] = 18.0

#: Log-normal sigma for per-user churn propensity.
CHURN_SIGMA: Final[float] = 0.38

#: Bounds on a perturbed session frequency, in sessions per week. The ceiling
#: keeps one extreme draw from generating a user with fifty thousand events.
FREQUENCY_BOUNDS: Final[tuple[float, float]] = (0.15, 14.0)


@dataclass(frozen=True, slots=True)
class UserProfile:
    """One user's realised behavioural parameters.

    Produced by :func:`build_profile` and consumed by the session and event
    generators. Everything is already perturbed, so the generators contain no
    randomness of their own beyond the draws these values parameterise.

    Attributes:
        persona: Persona name recorded in ``core.users``. For a graduated New
            Explorer this is the destination persona.
        initial_persona: Persona governing behaviour before graduation. Equal to
            :attr:`persona` for everyone else.
        graduation_day: Days after signup at which behaviour switches, or ``None``.
        behaviour: Behavioural parameters for :attr:`persona`.
        initial_behaviour: Behavioural parameters for :attr:`initial_persona`.
        sessions_per_week: This user's realised base session frequency.
        completion_rate: Probability of finishing a started title.
        churn_propensity: Monthly churn hazard before tenure and engagement
            multipliers.
        preferred_genres: Two or three genres this individual leans toward beyond
            their persona's affinity, so two users of the same persona are not
            interchangeable.
    """

    persona: str
    initial_persona: str
    graduation_day: int | None
    behaviour: PersonaBehaviour
    initial_behaviour: PersonaBehaviour
    sessions_per_week: float
    completion_rate: float
    churn_propensity: float
    preferred_genres: tuple[str, ...]

    def behaviour_at(self, days_since_signup: int) -> PersonaBehaviour:
        """Return the behaviour governing a given point in the user's life.

        Args:
            days_since_signup: Whole days since signup.

        Returns:
            The applicable :class:`PersonaBehaviour`.
        """
        if self.graduation_day is not None and days_since_signup < self.graduation_day:
            return self.initial_behaviour
        return self.behaviour

    def frequency_at(self, days_since_signup: int) -> float:
        """Return the session frequency at a point in the user's life.

        Applies the persona's monthly activity trend, which is what makes a Churn
        Risk's decline observable in the data rather than asserted in a docstring.

        Args:
            days_since_signup: Whole days since signup.

        Returns:
            Sessions per week, floored so the value stays positive.
        """
        behaviour = self.behaviour_at(days_since_signup)
        months = days_since_signup / 30.44
        trended = self.sessions_per_week * (behaviour.activity_trend**months)
        return max(trended, FREQUENCY_BOUNDS[0])


def _perturb_frequency(rng: Random, base: float) -> float:
    """Draw a per-user session frequency around a persona base.

    Args:
        rng: Seeded random source.
        base: The persona's ``base_sessions_per_week``.

    Returns:
        A frequency within :data:`FREQUENCY_BOUNDS`.
    """
    # Log-normal with mu = -sigma^2/2 so the *mean* of the multiplier is 1.0.
    # Using mu = 0 would inflate every persona's mean by exp(sigma^2/2), which
    # would quietly break agreement with the coefficients stored in core.personas.
    mu = -(FREQUENCY_SIGMA**2) / 2.0
    multiplier = rng.lognormvariate(mu, FREQUENCY_SIGMA)
    low, high = FREQUENCY_BOUNDS
    return min(max(base * multiplier, low), high)


def _perturb_rate(rng: Random, base: float, concentration: float) -> float:
    """Draw a per-user probability around a persona base.

    Uses a Beta distribution parameterised by mean and concentration, so the
    result stays inside ``(0, 1)`` without clamping and the group mean is
    preserved.

    Args:
        rng: Seeded random source.
        base: Target mean in ``(0, 1)``.
        concentration: Higher values cluster tighter around ``base``.

    Returns:
        A probability in ``(0.01, 0.99)``.
    """
    # Guard the degenerate ends: alpha or beta of zero is undefined.
    mean = min(max(base, 0.02), 0.98)
    alpha = mean * concentration
    beta = (1.0 - mean) * concentration
    return min(max(rng.betavariate(alpha, beta), 0.01), 0.99)


def _perturb_churn(rng: Random, base: float) -> float:
    """Draw a per-user churn propensity around a persona base.

    Args:
        rng: Seeded random source.
        base: The persona's ``base_churn_propensity``.

    Returns:
        A monthly hazard in ``(0.002, 0.85)``.
    """
    mu = -(CHURN_SIGMA**2) / 2.0
    multiplier = rng.lognormvariate(mu, CHURN_SIGMA)
    return min(max(base * multiplier, 0.002), 0.85)


def _draw_preferred_genres(
    rng: Random,
    behaviour: PersonaBehaviour,
    all_genres: tuple[str, ...],
) -> tuple[str, ...]:
    """Draw a small personal genre bias on top of the persona's affinity.

    Two users of the same persona should not have identical taste. This is what
    makes the genre-affinity-by-persona heatmap show a strong diagonal with real
    off-diagonal variation, rather than a set of hard blocks.

    Args:
        rng: Seeded random source.
        behaviour: The persona's behaviour, whose affinity biases the draw.
        all_genres: Every genre name from ``core.genres``.

    Returns:
        Two or three distinct genre names.
    """
    weights = [max(behaviour.genre_affinity.get(genre, 1.0), 0.05) for genre in all_genres]
    count = rng.choice((2, 2, 3))

    chosen: list[str] = []
    pool = list(all_genres)
    pool_weights = list(weights)
    for _ in range(min(count, len(pool))):
        pick = rng.choices(range(len(pool)), weights=pool_weights, k=1)[0]
        chosen.append(pool.pop(pick))
        pool_weights.pop(pick)

    return tuple(chosen)


def build_profile(
    rng: Random,
    *,
    persona: str,
    base_sessions_per_week: float,
    base_completion_rate: float,
    base_churn_propensity: float,
    all_genres: tuple[str, ...],
    observation_days: int,
) -> UserProfile:
    """Build one user's realised behaviour profile.

    Args:
        rng: Seeded random source.
        persona: Persona name assigned to this user.
        base_sessions_per_week: From ``core.personas``.
        base_completion_rate: From ``core.personas``.
        base_churn_propensity: From ``core.personas``.
        all_genres: Every genre name from ``core.genres``.
        observation_days: Days between this user's signup and the window end.
            A New Explorer observed for fewer than :data:`GRADUATION_DAYS` never
            graduates, because their graduation would fall outside the dataset.

    Returns:
        The realised :class:`UserProfile`.

    Raises:
        KeyError: If ``persona`` has no behavioural parameters, meaning
            ``core.personas`` and this module have drifted apart.
    """
    if persona not in PERSONA_BEHAVIOUR:
        known = ", ".join(sorted(PERSONA_BEHAVIOUR))
        raise KeyError(
            f"No behaviour defined for persona {persona!r}. Known personas: {known}. "
            "seeder/personas.py and Alembic revision 0002 have drifted apart."
        )

    initial_persona = persona
    graduation_day: int | None = None

    if persona == "New Explorer" and observation_days > GRADUATION_DAYS:
        # Graduate into a steady-state persona. The stored persona_id is the
        # destination, which is what a real analyst would see on a mature account.
        targets = list(GRADUATION_TARGETS)
        persona = rng.choices(
            targets, weights=[GRADUATION_TARGETS[name] for name in targets], k=1
        )[0]
        graduation_day = GRADUATION_DAYS

    behaviour = PERSONA_BEHAVIOUR[persona]
    initial_behaviour = PERSONA_BEHAVIOUR[initial_persona]

    return UserProfile(
        persona=persona,
        initial_persona=initial_persona,
        graduation_day=graduation_day,
        behaviour=behaviour,
        initial_behaviour=initial_behaviour,
        sessions_per_week=_perturb_frequency(rng, base_sessions_per_week),
        completion_rate=_perturb_rate(rng, base_completion_rate, COMPLETION_CONCENTRATION),
        churn_propensity=_perturb_churn(rng, base_churn_propensity),
        preferred_genres=_draw_preferred_genres(rng, behaviour, all_genres),
    )


def content_weight(
    profile: UserProfile,
    *,
    days_since_signup: int,
    genre: str,
    content_type: str,
    popularity: float,
    days_since_added: int,
) -> float:
    """Return this user's relative preference for one title.

    The event generator calls this for a candidate set and samples proportionally.
    Combining persona affinity, personal taste, popularity and recency in one place
    keeps the generator itself free of preference logic.

    Args:
        profile: The user's realised profile.
        days_since_signup: Used to select pre- or post-graduation behaviour.
        genre: The title's genre name.
        content_type: The title's ``core.content_type`` label.
        popularity: The title's 0-100 popularity score.
        days_since_added: Days between the title joining the catalogue and now.
            Negative values mean the title is not yet available.

    Returns:
        A non-negative weight. Zero means the title is unavailable.
    """
    if days_since_added < 0:
        return 0.0

    behaviour = profile.behaviour_at(days_since_signup)

    weight = behaviour.genre_affinity.get(genre, 1.0)
    weight *= behaviour.content_type_affinity.get(content_type, 1.0)

    # Personal taste on top of persona taste.
    if genre in profile.preferred_genres:
        weight *= 1.85

    # Popularity, exponentiated. Normalised to a 0-1 base first so the exponent
    # behaves consistently regardless of the raw scale.
    weight *= max(popularity / 100.0, 0.01) ** behaviour.popularity_bias

    # Recency decay with a 120-day half-life, scaled by the persona's appetite for
    # new releases. Sports decays fast; a prestige drama barely at all.
    recency = 0.5 ** (days_since_added / 120.0)
    weight *= 1.0 + (behaviour.recency_bias - 1.0) * recency

    return max(weight, 0.0)


def summarise_personas() -> dict[str, dict[str, float | str]]:
    """Return a per-persona summary for the data-quality report.

    Returns:
        Mapping of persona name to its headline behavioural parameters, so the
        report can show what each archetype was configured to do next to what the
        generated data actually shows.
    """
    return {
        name: {
            "search_bias": behaviour.search_bias,
            "playback_probability": behaviour.playback_probability,
            "modal_session_minutes": behaviour.session_minutes[1],
            "modal_episodes_per_sitting": behaviour.episodes_per_sitting[1],
            "popularity_bias": behaviour.popularity_bias,
            "monthly_activity_trend": behaviour.activity_trend,
            "top_genre": max(
                behaviour.genre_affinity,
                key=lambda genre: behaviour.genre_affinity[genre],
                default="(flat)",
            ),
        }
        for name, behaviour in PERSONA_BEHAVIOUR.items()
    }


__all__ = [
    "CHURN_SIGMA",
    "COMPLETION_CONCENTRATION",
    "FREQUENCY_BOUNDS",
    "FREQUENCY_SIGMA",
    "GRADUATION_DAYS",
    "GRADUATION_TARGETS",
    "PERSONA_BEHAVIOUR",
    "PersonaBehaviour",
    "UserProfile",
    "build_profile",
    "content_weight",
    "summarise_personas",
]
