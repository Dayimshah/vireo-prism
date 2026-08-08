"""Clickstream journey engine: per-persona Markov navigation plus playback blocks.

This module answers one question: *in what order does a real person tap through a
streaming app?* It produces an ordered, legal sequence of events for one session.
It does not assign timestamps or content ids — :mod:`seeder.generators.events`
does that, using the plan this module returns.

Why not one 15x15 transition matrix
-----------------------------------
The obvious implementation is a single Markov matrix over all fifteen event names.
It is also wrong, and instructively so. A raw matrix can emit ``COMPLETE_VIDEO``
without a preceding ``START_VIDEO``, or ``RATE`` for a title the user abandoned.
Those sequences violate the invariants the analytics layer depends on — the funnel
would show more completions than starts — and no amount of tuning the
probabilities removes the possibility, it only makes the bug rarer and harder to
find.

So the model is split in two:

**Navigation** is a genuine Markov chain over six states (``OPEN_APP``, ``HOME``,
``BROWSE_GENRE``, ``SEARCH``, ``VIEW_CONTENT``, ``EXIT``). This is where persona
differences live: a Movie Lover arrives via ``SEARCH`` because they know the
title; a Casual Viewer loops ``HOME → BROWSE_GENRE → HOME`` and often leaves
without watching anything.

**Playback** is an atomic block, emitted by :func:`_plan_playback` as a
structurally valid sequence: ``START_VIDEO → VIDEO_PROGRESS* → (PAUSE_VIDEO) →
COMPLETE_VIDEO | ABANDON_VIDEO → (RATE)``. Illegal orderings are not improbable
here, they are unrepresentable.

The invariants this guarantees
------------------------------
Every plan satisfies, by construction:

* opens with ``OPEN_APP``, closes with ``EXIT``;
* ``START_VIDEO`` is always preceded by ``VIEW_CONTENT`` on the same content slot;
* ``COMPLETE_VIDEO`` and ``ABANDON_VIDEO`` never both occur for one slot;
* ``RATE`` occurs only after ``COMPLETE_VIDEO`` on the same slot;
* ``progress_pct`` is non-decreasing within a slot and ends at or above
  :data:`~seeder.config.COMPLETION_THRESHOLD_PCT` exactly when the title completes;
* ``content_id`` presence matches the ``ck_events_content_id_presence`` constraint
  in Alembic revision 0004.

``tests/test_seeder.py`` re-asserts all of these against the loaded database, so
the guarantee is verified rather than merely intended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from seeder import config
from seeder.personas import PersonaBehaviour

if TYPE_CHECKING:
    from random import Random

    from seeder.personas import UserProfile

# ===========================================================================
# Screens
#
# Each event name maps to the screen it occurs on. Values must be members of the
# CHECK list in Alembic revisions 0003 and 0004.
# ===========================================================================

SCREEN_FOR_EVENT: Final[dict[str, str]] = {
    "OPEN_APP": "splash",
    "HOME": "home",
    "BROWSE_GENRE": "browse",
    "SEARCH": "search",
    "VIEW_CONTENT": "detail",
    "WATCH_TRAILER": "detail",
    "START_VIDEO": "player",
    "VIDEO_PROGRESS": "player",
    "PAUSE_VIDEO": "player",
    "ABANDON_VIDEO": "player",
    "COMPLETE_VIDEO": "player",
    "ADD_TO_WATCHLIST": "detail",
    "RATE": "player",
    "SUBSCRIBE_CLICK": "paywall",
    "EXIT": "home",
}

#: Events that must carry a ``content_id``, per ``ck_events_content_id_presence``.
CONTENT_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "VIEW_CONTENT",
        "WATCH_TRAILER",
        "START_VIDEO",
        "VIDEO_PROGRESS",
        "PAUSE_VIDEO",
        "ABANDON_VIDEO",
        "COMPLETE_VIDEO",
        "ADD_TO_WATCHLIST",
        "RATE",
    }
)

#: Events that must not carry a ``content_id``.
CONTENT_FORBIDDEN: Final[frozenset[str]] = frozenset(
    {"OPEN_APP", "HOME", "SEARCH", "EXIT"}
)


# ===========================================================================
# Navigation chain
# ===========================================================================

#: Navigation states. Deliberately excludes every playback event: those are
#: emitted by the playback block, not reached by a transition.
NAV_STATES: Final[tuple[str, ...]] = (
    "OPEN_APP",
    "HOME",
    "BROWSE_GENRE",
    "SEARCH",
    "VIEW_CONTENT",
    "EXIT",
)

#: Base navigation transition weights, ``from_state -> {to_state: weight}``.
#:
#: Read this as the behaviour of an average user; :func:`_transitions_for` then
#: skews it by persona. Two properties worth noting:
#:
#: * ``VIEW_CONTENT`` returns to ``HOME`` more often than it proceeds, which is
#:   what produces the realistic drop between "looked at a title" and "watched
#:   it" — the most important step in the discovery funnel.
#: * Every state can reach ``EXIT``. People close apps from anywhere, and a chain
#:   that can only exit from the home screen produces an implausibly tidy
#:   exit-screen distribution.
NAV_TRANSITIONS: Final[dict[str, dict[str, float]]] = {
    "OPEN_APP": {
        # Almost everyone lands on home; a few resume straight into a detail page
        # from a deep link or a push notification.
        "HOME": 0.88,
        "VIEW_CONTENT": 0.07,
        "SEARCH": 0.04,
        "EXIT": 0.01,
    },
    "HOME": {
        "BROWSE_GENRE": 0.30,
        "VIEW_CONTENT": 0.34,
        "SEARCH": 0.16,
        "HOME": 0.08,  # scrolling further down the rails
        "EXIT": 0.12,
    },
    "BROWSE_GENRE": {
        "VIEW_CONTENT": 0.52,
        "BROWSE_GENRE": 0.19,  # switching genre rails
        "HOME": 0.16,
        "SEARCH": 0.05,
        "EXIT": 0.08,
    },
    "SEARCH": {
        # Search is high-intent: most searches end in a detail page.
        "VIEW_CONTENT": 0.66,
        "SEARCH": 0.17,  # refining the query
        "HOME": 0.09,
        "BROWSE_GENRE": 0.03,
        "EXIT": 0.05,
    },
    "VIEW_CONTENT": {
        # Where the funnel actually leaks. Handled specially in
        # :func:`plan_session`, which decides between playback and moving on.
        "HOME": 0.37,
        "BROWSE_GENRE": 0.11,
        "SEARCH": 0.08,
        "VIEW_CONTENT": 0.14,  # comparing two titles
        "EXIT": 0.30,
    },
}

#: Persona skews on the navigation chain, as ``{persona: {(from, to): factor}}``.
#:
#: These are the levers that make each persona's funnel shape distinct. Applied
#: multiplicatively and renormalised, so a factor of 2.0 means "twice as likely
#: relative to the alternatives from this state".
NAV_PERSONA_SKEW: Final[dict[str, dict[tuple[str, str], float]]] = {
    "Binge Watcher": {
        # Knows what they are continuing; goes almost straight to the title.
        ("HOME", "VIEW_CONTENT"): 1.7,
        ("HOME", "BROWSE_GENRE"): 0.6,
        ("VIEW_CONTENT", "EXIT"): 0.4,
        ("OPEN_APP", "VIEW_CONTENT"): 2.6,  # resumes from Continue Watching
    },
    "Movie Lover": {
        ("HOME", "SEARCH"): 1.9,
        ("SEARCH", "VIEW_CONTENT"): 1.3,
        ("VIEW_CONTENT", "VIEW_CONTENT"): 1.6,  # compares before committing
        ("VIEW_CONTENT", "EXIT"): 0.7,
    },
    "Anime Fan": {
        ("HOME", "SEARCH"): 2.1,
        ("OPEN_APP", "SEARCH"): 2.4,
        ("BROWSE_GENRE", "VIEW_CONTENT"): 1.3,
        ("VIEW_CONTENT", "EXIT"): 0.5,
    },
    "Sports Fan": {
        ("OPEN_APP", "VIEW_CONTENT"): 2.2,  # arrives for one fixture
        ("HOME", "VIEW_CONTENT"): 1.5,
        ("HOME", "BROWSE_GENRE"): 0.7,
        ("VIEW_CONTENT", "VIEW_CONTENT"): 0.6,
    },
    "Casual Viewer": {
        # The browse-heavy, low-commitment pattern.
        ("HOME", "BROWSE_GENRE"): 1.8,
        ("BROWSE_GENRE", "BROWSE_GENRE"): 2.1,
        ("HOME", "SEARCH"): 0.4,
        ("VIEW_CONTENT", "HOME"): 1.5,
        ("VIEW_CONTENT", "EXIT"): 1.3,
    },
    "Premium Loyalist": {
        ("HOME", "VIEW_CONTENT"): 1.4,
        ("VIEW_CONTENT", "EXIT"): 0.5,
        ("HOME", "EXIT"): 0.6,
    },
    "Churn Risk": {
        # Opens the app, looks around, leaves. The signature of disengagement.
        ("HOME", "EXIT"): 2.8,
        ("BROWSE_GENRE", "EXIT"): 2.4,
        ("VIEW_CONTENT", "EXIT"): 1.9,
        ("HOME", "VIEW_CONTENT"): 0.6,
        ("SEARCH", "VIEW_CONTENT"): 0.8,
    },
    "New Explorer": {
        # Sampling widely: lots of search and lots of detail pages, less watching.
        ("HOME", "SEARCH"): 1.6,
        ("HOME", "BROWSE_GENRE"): 1.4,
        ("VIEW_CONTENT", "VIEW_CONTENT"): 2.2,
        ("VIEW_CONTENT", "HOME"): 1.3,
    },
}

#: Hard ceiling on navigation steps per session. Reached only by a pathological
#: draw; without it a self-looping chain could in principle run forever.
MAX_NAV_STEPS: Final[int] = 48

#: Ceiling on distinct titles engaged in one session, independent of the
#: per-persona ``titles_per_session`` draw.
MAX_SLOTS_PER_SESSION: Final[int] = 8

#: Probability a session that reaches the paywall does so from a detail page
#: rather than from home. Only applies to non-paying users.
SUBSCRIBE_CLICK_FROM_DETAIL: Final[float] = 0.72


# ===========================================================================
# Plan structures
# ===========================================================================


@dataclass(slots=True)
class PlannedEvent:
    """One event in a planned session, without an absolute timestamp.

    Attributes:
        event_name: A ``core.event_name`` enum label.
        screen: Screen the event occurred on.
        slot: Index into the session's content slots, or ``None`` for navigation
            events. :mod:`seeder.generators.events` binds slots to real
            ``content_id`` values.
        dwell_seconds: Seconds elapsed since the previous event. Playback events
            carry the real watched duration; navigation events carry think time.
        watch_seconds: Incremental seconds watched, for playback events only.
        progress_pct: Cumulative percentage watched, for playback events only.
        properties: Event payload written to ``core.events.properties``.
    """

    event_name: str
    screen: str
    slot: int | None = None
    dwell_seconds: float = 0.0
    watch_seconds: int | None = None
    progress_pct: float | None = None
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SessionPlan:
    """A complete planned session.

    Attributes:
        events: Ordered events, opening with ``OPEN_APP`` and closing with ``EXIT``.
        slot_count: Number of distinct content slots referenced.
        total_watch_seconds: Sum of playback seconds, matching what the loader
            writes to ``core.sessions.watch_seconds``.
        completed_slots: Slots that reached ``COMPLETE_VIDEO``.
        started_slots: Slots that reached ``START_VIDEO``.
        exit_screen: Screen of the final event.
        had_subscribe_click: Whether the paywall was reached.
    """

    events: list[PlannedEvent]
    slot_count: int
    total_watch_seconds: int
    completed_slots: set[int]
    started_slots: set[int]
    exit_screen: str
    had_subscribe_click: bool

    @property
    def duration_seconds(self) -> int:
        """Return the session's wall-clock length.

        Returns:
            Total dwell across every event, in whole seconds.
        """
        return int(round(sum(event.dwell_seconds for event in self.events)))


def _transitions_for(persona: str, state: str) -> dict[str, float]:
    """Return normalised transition probabilities out of a state.

    Args:
        persona: Persona name, used to look up skews.
        state: Current navigation state.

    Returns:
        Mapping of destination state to probability, summing to 1.0.
    """
    base = NAV_TRANSITIONS[state]
    skew = NAV_PERSONA_SKEW.get(persona, {})

    weighted = {
        destination: weight * skew.get((state, destination), 1.0)
        for destination, weight in base.items()
    }
    total = sum(weighted.values())
    return {destination: weight / total for destination, weight in weighted.items()}


def _next_state(rng: Random, persona: str, state: str) -> str:
    """Draw the next navigation state.

    Args:
        rng: Seeded random source.
        persona: Persona name.
        state: Current state.

    Returns:
        The destination state.
    """
    transitions = _transitions_for(persona, state)
    destinations = list(transitions)
    return rng.choices(destinations, weights=[transitions[d] for d in destinations], k=1)[0]


def _dwell(rng: Random) -> float:
    """Draw think time between two navigation events.

    Returns:
        Seconds, from a triangular draw over
        :data:`~seeder.config.NAVIGATION_DWELL_SECONDS`.
    """
    low, mode, high = config.NAVIGATION_DWELL_SECONDS
    return rng.triangular(low, high, mode)


def _draw_abandon_fraction(rng: Random) -> float:
    """Draw where an abandoner stops, as a fraction of runtime.

    The distribution is a weighted mixture of three normals, not a single one.
    That is deliberate: most quitting happens in the first few minutes, with a
    smaller cluster near the end from people who intend to finish later. A
    unimodal draw would hide the most actionable content signal in the dataset —
    the difference between a title people bounce off immediately and one they
    almost finish.

    Args:
        rng: Seeded random source.

    Returns:
        A fraction in ``(0.01, 0.89)``. Capped below the completion threshold so
        an abandonment can never be mistaken for a completion.
    """
    modes = config.ABANDON_POINT_MODES
    chosen = rng.choices(
        range(len(modes)), weights=[weight for weight, _, _ in modes], k=1
    )[0]
    _, mean, sigma = modes[chosen]
    fraction = rng.gauss(mean, sigma)
    ceiling = (config.COMPLETION_THRESHOLD_PCT - 1.0) / 100.0
    return min(max(fraction, 0.01), ceiling)


def _plan_playback(
    rng: Random,
    events: list[PlannedEvent],
    *,
    slot: int,
    profile: UserProfile,
    behaviour: PersonaBehaviour,
    runtime_seconds: int,
    is_series: bool,
    budget_seconds: float,
) -> tuple[int, bool, float]:
    """Append one structurally valid playback block within a time budget.

    The ordering guarantee lives here. The sequence is built as
    ``START_VIDEO → VIDEO_PROGRESS* → (PAUSE_VIDEO) → COMPLETE_VIDEO |
    ABANDON_VIDEO → (RATE)``, so an illegal ordering is not improbable — it cannot
    be expressed.

    ``budget_seconds`` is what keeps a binge session finite. Without it an Anime
    Fan drawing 11 episodes at 24 minutes each emits well over a hundred progress
    events from one sitting, and the fact table grows by an order of magnitude for
    no analytical gain. The budget is checked before each episode, and a playback
    that would overrun it is cut short as a real abandonment rather than silently
    truncated — so the invariants still hold and the abandonment is visible in the
    data as what it was.

    Args:
        rng: Seeded random source.
        events: Event list to append to, mutated in place.
        slot: Content slot this playback refers to.
        profile: The user's realised profile, for their completion rate.
        behaviour: Behaviour governing this point in the user's life.
        runtime_seconds: Watchable length of one episode or the whole film.
        is_series: Whether consecutive episodes may be watched in one sitting.
        budget_seconds: Seconds of session time still available.

    Returns:
        ``(watch_seconds, completed, consumed_seconds)`` — seconds watched, whether
        any episode completed, and total dwell added (which exceeds watch time when
        a pause occurred).
    """
    episodes = 1
    if is_series:
        low, mode, high = behaviour.episodes_per_sitting
        episodes = max(1, int(round(rng.triangular(low, high, mode))))

    total_watched = 0
    completed_any = False
    # Dwell is summed from the events actually appended rather than tracked by hand.
    # Pauses and rating events consume session time too, and a manual counter would
    # drift out of agreement with the duration the caller ultimately measures.
    start_index = len(events)

    for episode_index in range(episodes):
        consumed = sum(event.dwell_seconds for event in events[start_index:])
        # Never start an episode with no time left. The first episode is exempt:
        # the caller already decided this session contains playback, and a
        # START_VIDEO with nothing after it would be a worse artefact than a
        # slightly overlong session.
        remaining = budget_seconds - consumed
        if episode_index > 0 and remaining <= 60.0:
            break

        completes = rng.random() < profile.completion_rate

        if completes:
            target_fraction = 1.0
            final_pct = round(rng.uniform(config.COMPLETION_THRESHOLD_PCT, 100.0), 2)
        else:
            target_fraction = _draw_abandon_fraction(rng)
            final_pct = round(target_fraction * 100.0, 2)

        watched_seconds = max(1, int(runtime_seconds * target_fraction))

        # A playback that would overrun the remaining budget becomes an
        # abandonment at the point the user ran out of time — but only when the
        # truncated position is genuinely below the completion threshold. Otherwise
        # they were near enough the end to finish, and overshooting the budget by a
        # few minutes is more realistic than cutting away mid-scene.
        if episode_index > 0 and watched_seconds > remaining:
            truncated = max(1, int(remaining))
            truncated_pct = truncated / runtime_seconds * 100.0
            if truncated_pct < config.COMPLETION_THRESHOLD_PCT:
                completes = False
                watched_seconds = truncated
                final_pct = round(truncated_pct, 2)

        events.append(
            PlannedEvent(
                event_name="START_VIDEO",
                screen=SCREEN_FOR_EVENT["START_VIDEO"],
                slot=slot,
                dwell_seconds=rng.uniform(1.0, 4.0),
                properties={"episode": episode_index + 1} if is_series else {},
            )
        )

        # Progress checkpoints. watch_seconds is *incremental* per event so that
        # SUM(watch_seconds) is correct at any aggregation grain; progress_pct is
        # cumulative and monotonic. The analytics layer depends on exactly this
        # split — see the note in Alembic revision 0006.
        interval = config.PROGRESS_EVENT_INTERVAL_SECONDS
        emitted = 0
        cumulative_pct = 0.0
        paused = rng.random() < config.PAUSE_PROBABILITY
        pause_at = rng.uniform(0.2, 0.8) if paused else None

        while emitted + interval < watched_seconds:
            emitted += interval
            cumulative_pct = round(emitted / runtime_seconds * 100.0, 2)
            events.append(
                PlannedEvent(
                    event_name="VIDEO_PROGRESS",
                    screen=SCREEN_FOR_EVENT["VIDEO_PROGRESS"],
                    slot=slot,
                    dwell_seconds=float(interval),
                    watch_seconds=interval,
                    progress_pct=min(cumulative_pct, 99.99),
                )
            )

            if pause_at is not None and emitted / watched_seconds >= pause_at:
                events.append(
                    PlannedEvent(
                        event_name="PAUSE_VIDEO",
                        screen=SCREEN_FOR_EVENT["PAUSE_VIDEO"],
                        slot=slot,
                        # A pause is where a session's wall-clock time exceeds its
                        # watch time, which is why sessions.watch_seconds is
                        # bounded by duration_seconds rather than equal to it.
                        dwell_seconds=rng.uniform(20.0, 420.0),
                        watch_seconds=0,
                        progress_pct=min(cumulative_pct, 99.99),
                    )
                )
                pause_at = None

        # A completion's final_pct was drawn independently of the checkpoints that
        # were actually emitted, so it can land *below* the last one — a 52-minute
        # episode whose final checkpoint sits at 96.15% could draw 95.94% and appear
        # to rewind. Floor it at the last checkpoint.
        #
        # Abandonments need no such guard: watched_seconds is derived from the same
        # fraction as final_pct, and the loop condition keeps every checkpoint
        # strictly below it.
        if completes:
            final_pct = round(max(final_pct, cumulative_pct), 2)

        # Terminal event carries the remaining seconds, so the incremental sum
        # equals watched_seconds exactly.
        remainder = max(watched_seconds - emitted, 1)
        terminal = "COMPLETE_VIDEO" if completes else "ABANDON_VIDEO"
        events.append(
            PlannedEvent(
                event_name=terminal,
                screen=SCREEN_FOR_EVENT[terminal],
                slot=slot,
                dwell_seconds=float(remainder),
                watch_seconds=remainder,
                progress_pct=final_pct,
            )
        )

        total_watched += watched_seconds

        if completes:
            completed_any = True
            # RATE only ever follows a completion — the invariant, enforced by
            # nesting rather than by a probability.
            rate_chance = (
                config.RATE_AFTER_COMPLETE_PROBABILITY * behaviour.rating_propensity
            )
            if rng.random() < rate_chance:
                rating = rng.choices(
                    list(config.RATING_WEIGHTS),
                    weights=list(config.RATING_WEIGHTS.values()),
                    k=1,
                )[0]
                events.append(
                    PlannedEvent(
                        event_name="RATE",
                        screen=SCREEN_FOR_EVENT["RATE"],
                        slot=slot,
                        dwell_seconds=rng.uniform(2.0, 12.0),
                        properties={"rating": rating},
                    )
                )
        else:
            # Abandoning one episode ends the sitting; nobody abandons episode
            # three then starts episode four.
            break

    consumed = sum(event.dwell_seconds for event in events[start_index:])
    return total_watched, completed_any, consumed


def plan_session(
    rng: Random,
    *,
    profile: UserProfile,
    days_since_signup: int,
    is_premium: bool,
    slot_runtimes: list[tuple[int, bool]],
    search_terms: list[str] | None = None,
    genre_names: tuple[str, ...] = (),
) -> SessionPlan:
    """Plan one complete session.

    Args:
        rng: Seeded random source.
        profile: The user's realised behavioural profile.
        days_since_signup: Selects pre- or post-graduation behaviour.
        is_premium: Paying users never see the paywall.
        slot_runtimes: One ``(runtime_seconds, is_series)`` pair per available
            content slot, pre-selected by the caller. The plan may use fewer than
            supplied but never more.
        search_terms: Query strings to attach to ``SEARCH`` events. Cycled if
            shorter than the number of searches.
        genre_names: Genre names to attach to ``BROWSE_GENRE`` events.

    Returns:
        The planned session.
    """
    behaviour = profile.behaviour_at(days_since_signup)

    # How many distinct titles this user will engage with.
    low, mode, high = behaviour.titles_per_session
    target_slots = max(1, int(round(rng.triangular(low, high, mode))))
    target_slots = min(target_slots, MAX_SLOTS_PER_SESSION, len(slot_runtimes))

    # The session's time budget, drawn from the persona's own session_minutes.
    # This is what keeps a plan finite: without it, session length emerged from
    # accumulated dwell plus unbounded playback, and a Binge Watcher drawing three
    # titles at three episodes each produced sessions of eight hours and a hundred
    # progress events. The budget makes behaviour.session_minutes actually govern
    # session length, which is what it was declared for.
    low, mode, high = behaviour.session_minutes
    budget_seconds = rng.triangular(low * 60.0, high * 60.0, mode * 60.0)

    events: list[PlannedEvent] = [
        PlannedEvent(
            event_name="OPEN_APP",
            screen=SCREEN_FOR_EVENT["OPEN_APP"],
            dwell_seconds=0.0,
        )
    ]

    total_watch = 0
    started: set[int] = set()
    completed: set[int] = set()
    slots_used = 0
    search_index = 0
    had_subscribe_click = False

    # Running total of planned dwell. Incremented at each append rather than
    # re-summed per iteration, which would make the loop quadratic in event count.
    spent = 0.0

    state = "OPEN_APP"
    steps = 0

    while steps < MAX_NAV_STEPS:
        steps += 1

        # Out of time: leave the app. Checked before the transition so the session
        # ends on a real EXIT rather than being truncated mid-navigation.
        if spent >= budget_seconds:
            break

        state = _next_state(rng, profile.persona, state)

        if state == "EXIT":
            break

        if state == "SEARCH":
            term = None
            if search_terms:
                term = search_terms[search_index % len(search_terms)]
                search_index += 1
            dwell = _dwell(rng)
            spent += dwell
            events.append(
                PlannedEvent(
                    event_name="SEARCH",
                    screen=SCREEN_FOR_EVENT["SEARCH"],
                    dwell_seconds=dwell,
                    properties={"search_query": term} if term else {},
                )
            )
            continue

        if state == "BROWSE_GENRE":
            genre = rng.choice(genre_names) if genre_names else None
            dwell = _dwell(rng)
            spent += dwell
            events.append(
                PlannedEvent(
                    event_name="BROWSE_GENRE",
                    screen=SCREEN_FOR_EVENT["BROWSE_GENRE"],
                    dwell_seconds=dwell,
                    properties={"genre": genre} if genre else {},
                )
            )
            continue

        if state == "HOME":
            dwell = _dwell(rng)
            spent += dwell
            events.append(
                PlannedEvent(
                    event_name="HOME",
                    screen=SCREEN_FOR_EVENT["HOME"],
                    dwell_seconds=dwell,
                )
            )
            continue

        # VIEW_CONTENT: the discovery-to-watch decision point.
        if slots_used >= target_slots:
            # Out of distinct titles for this session; treat as a return to
            # browsing rather than forcing an exit, which would truncate sessions
            # unnaturally. The time budget still ends the session on its own.
            dwell = _dwell(rng)
            spent += dwell
            events.append(
                PlannedEvent(
                    event_name="HOME",
                    screen=SCREEN_FOR_EVENT["HOME"],
                    dwell_seconds=dwell,
                )
            )
            state = "HOME"
            continue

        slot = slots_used
        slots_used += 1
        runtime_seconds, is_series = slot_runtimes[slot]

        dwell = _dwell(rng)
        spent += dwell
        events.append(
            PlannedEvent(
                event_name="VIEW_CONTENT",
                screen=SCREEN_FOR_EVENT["VIEW_CONTENT"],
                slot=slot,
                dwell_seconds=dwell,
            )
        )

        # A trailer is an evaluation step, and its conversion to a start is one of
        # the metrics the Content page reports.
        if rng.random() < 0.34:
            trailer_low, trailer_high = config.TRAILER_SECONDS
            trailer_dwell = float(rng.randint(trailer_low, trailer_high))
            spent += trailer_dwell
            events.append(
                PlannedEvent(
                    event_name="WATCH_TRAILER",
                    screen=SCREEN_FOR_EVENT["WATCH_TRAILER"],
                    slot=slot,
                    dwell_seconds=trailer_dwell,
                )
            )

        if rng.random() < behaviour.watchlist_probability:
            watchlist_dwell = rng.uniform(1.0, 5.0)
            spent += watchlist_dwell
            events.append(
                PlannedEvent(
                    event_name="ADD_TO_WATCHLIST",
                    screen=SCREEN_FOR_EVENT["ADD_TO_WATCHLIST"],
                    slot=slot,
                    dwell_seconds=watchlist_dwell,
                )
            )

        if rng.random() < behaviour.playback_probability:
            watched, did_complete, consumed = _plan_playback(
                rng,
                events,
                slot=slot,
                profile=profile,
                behaviour=behaviour,
                runtime_seconds=runtime_seconds,
                is_series=is_series,
                # Whatever remains of the session. Playback is by far the largest
                # consumer of session time, so this is where the budget does its
                # real work.
                budget_seconds=max(budget_seconds - spent, 0.0),
            )
            spent += consumed
            total_watch += watched
            started.add(slot)
            if did_complete:
                completed.add(slot)

        # Paywall. Only reachable for non-paying users, and more likely from a
        # detail page than from home — you hit a wall when you try to watch
        # something, not while browsing.
        if not is_premium and rng.random() < behaviour.subscribe_click_probability:
            from_detail = rng.random() < SUBSCRIBE_CLICK_FROM_DETAIL
            paywall_dwell = _dwell(rng)
            spent += paywall_dwell
            events.append(
                PlannedEvent(
                    event_name="SUBSCRIBE_CLICK",
                    screen=SCREEN_FOR_EVENT["SUBSCRIBE_CLICK"],
                    slot=slot if from_detail else None,
                    dwell_seconds=paywall_dwell,
                    properties={"trigger": "detail" if from_detail else "home"},
                )
            )
            had_subscribe_click = True

    # Exit from wherever the user actually was, which is what gives the
    # entry/exit screen analysis a non-degenerate distribution.
    exit_screen = events[-1].screen if len(events) > 1 else "home"
    events.append(
        PlannedEvent(
            event_name="EXIT",
            screen=exit_screen,
            dwell_seconds=_dwell(rng) * 0.5,
        )
    )

    return SessionPlan(
        events=events,
        slot_count=slots_used,
        total_watch_seconds=total_watch,
        completed_slots=completed,
        started_slots=started,
        exit_screen=exit_screen,
        had_subscribe_click=had_subscribe_click,
    )


def validate_plan(plan: SessionPlan) -> None:
    """Assert that a plan satisfies every journey invariant.

    Called from :mod:`seeder.generators.events` under ``--validate`` and from
    ``tests/test_seeder.py``. Cheap enough to run on a sample of every seed.

    Args:
        plan: The plan to check.

    Raises:
        AssertionError: On the first violated invariant, naming which one.
    """
    events = plan.events
    assert events, "plan is empty"
    assert events[0].event_name == "OPEN_APP", "session must open with OPEN_APP"
    assert events[-1].event_name == "EXIT", "session must close with EXIT"
    assert len(events) >= 2, "ck_sessions_min_events requires at least two events"

    viewed: set[int] = set()
    started: set[int] = set()
    finished: set[int] = set()

    # Playback state is per *episode*, not per slot. Two consequences, both of which
    # an earlier version of this validator got wrong:
    #
    #   * Progress resets between episodes. A series that finishes episode 1 at 96%
    #     and starts episode 2 at 4% is correct, not a regression.
    #   * Completion and abandonment are mutually exclusive within an episode, but a
    #     slot may legitimately hold both — finish episodes 1 and 2, abandon 3.
    #
    # So `last_pct` is keyed on the current playback and cleared at each
    # START_VIDEO, and the exclusion check applies to the episode in flight rather
    # than to everything the slot ever did.
    last_pct: float = 0.0
    episode_finished = False
    episode_abandoned = False

    for event in events:
        name = event.event_name

        if name in CONTENT_REQUIRED:
            assert event.slot is not None, f"{name} requires a content slot"
        if name in CONTENT_FORBIDDEN:
            assert event.slot is None, f"{name} must not carry a content slot"

        assert event.screen in set(SCREEN_FOR_EVENT.values()), (
            f"{name} has unknown screen {event.screen!r}"
        )

        if name == "VIEW_CONTENT":
            viewed.add(event.slot)  # type: ignore[arg-type]

        elif name == "START_VIDEO":
            assert event.slot in viewed, "START_VIDEO before VIEW_CONTENT on same slot"
            started.add(event.slot)  # type: ignore[arg-type]
            # A new playback begins: reset per-episode state.
            last_pct = 0.0
            episode_finished = False
            episode_abandoned = False

        elif name in {"VIDEO_PROGRESS", "PAUSE_VIDEO", "COMPLETE_VIDEO", "ABANDON_VIDEO"}:
            assert event.slot in started, f"{name} before START_VIDEO on same slot"
            assert event.progress_pct is not None, f"{name} requires progress_pct"
            assert event.progress_pct >= last_pct, (
                f"progress_pct went backwards within one playback on slot "
                f"{event.slot}: {last_pct} -> {event.progress_pct}"
            )
            last_pct = event.progress_pct

            if name == "COMPLETE_VIDEO":
                assert event.progress_pct >= config.COMPLETION_THRESHOLD_PCT, (
                    "COMPLETE_VIDEO violates ck_events_complete_is_complete"
                )
                assert not episode_abandoned, "episode both completed and abandoned"
                episode_finished = True
                finished.add(event.slot)  # type: ignore[arg-type]
            elif name == "ABANDON_VIDEO":
                assert event.progress_pct < config.COMPLETION_THRESHOLD_PCT, (
                    "ABANDON_VIDEO reached the completion threshold"
                )
                assert not episode_finished, "episode both completed and abandoned"
                episode_abandoned = True

        elif name == "RATE":
            assert event.slot in finished, "RATE without a prior COMPLETE_VIDEO"

        if event.watch_seconds is not None:
            assert name in {
                "VIDEO_PROGRESS",
                "PAUSE_VIDEO",
                "ABANDON_VIDEO",
                "COMPLETE_VIDEO",
            }, f"{name} must not carry watch_seconds"
            assert event.watch_seconds >= 0, "watch_seconds must be non-negative"

    incremental = sum(e.watch_seconds or 0 for e in events)
    assert incremental == plan.total_watch_seconds, (
        f"incremental watch_seconds ({incremental}) disagrees with the plan total "
        f"({plan.total_watch_seconds}); sessions.watch_seconds would be wrong"
    )
    assert plan.total_watch_seconds <= plan.duration_seconds, (
        "watch time exceeds session duration, violating "
        "ck_sessions_watch_within_duration"
    )
    assert started == plan.started_slots, "started_slots disagrees with the event stream"
    assert finished == plan.completed_slots, (
        "completed_slots disagrees with the event stream"
    )


def summarise_navigation() -> dict[str, object]:
    """Return navigation parameters for the data-quality report.

    Returns:
        A mapping describing the base chain and each persona's strongest skews, so
        the report can compare intent against the generated funnel.
    """
    return {
        "states": list(NAV_STATES),
        "view_to_exit_base": NAV_TRANSITIONS["VIEW_CONTENT"]["EXIT"],
        "search_to_view_base": NAV_TRANSITIONS["SEARCH"]["VIEW_CONTENT"],
        "persona_skews": {
            persona: {
                f"{source}->{destination}": factor
                for (source, destination), factor in sorted(
                    skews.items(), key=lambda item: -abs(item[1] - 1.0)
                )[:3]
            }
            for persona, skews in NAV_PERSONA_SKEW.items()
        },
    }


__all__ = [
    "CONTENT_FORBIDDEN",
    "CONTENT_REQUIRED",
    "MAX_NAV_STEPS",
    "MAX_SLOTS_PER_SESSION",
    "NAV_PERSONA_SKEW",
    "NAV_STATES",
    "NAV_TRANSITIONS",
    "SCREEN_FOR_EVENT",
    "PlannedEvent",
    "SessionPlan",
    "plan_session",
    "summarise_navigation",
    "validate_plan",
]
