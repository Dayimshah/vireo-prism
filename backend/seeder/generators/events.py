"""Turn a planned session into concrete event rows.

:mod:`seeder.journeys` decides *what happens in what order*. This module decides
*when it happened and to which title*, which is the remaining half of an event row.

Three responsibilities:

**Content selection.** For each slot in a plan, pick a real title weighted by
:func:`seeder.personas.content_weight`. Titles not yet in the catalogue on the
session date are excluded outright, so no user watches something before Vireo
licensed it.

**Timestamp assignment.** Dwell times from the plan are accumulated into absolute
UTC timestamps. Strictly increasing within a session, which is what makes
``step_index`` and ``event_time`` agree and keeps the funnel ordering
deterministic.

**Ceiling enforcement.** No event may exceed the window end, because
``ck_events_no_future_time`` rejects it and a failed ``COPY`` at row three million
is an unpleasant way to discover a boundary bug. When a session would overrun, it
is compressed rather than truncated: dropping the tail would silently bias every
session-length percentile at the right edge of the dataset.

Search terms
------------
``SEARCH`` events carry a real query string in ``properties``, drawn from actual
catalogue titles with realistic mistakes — partial titles, lowercase, occasional
typos. That makes ``search_query`` worth indexing and the top-searches list worth
displaying, rather than a column full of ``"query"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from seeder.journeys import SessionPlan
from seeder.personas import content_weight

if TYPE_CHECKING:
    from random import Random

    from seeder.catalog import ContentRow
    from seeder.personas import UserProfile

#: Seconds added when a plan's accumulated dwell would place two events at the
#: same instant. Timestamps must be strictly increasing for ``step_index`` and
#: ``event_time`` to agree on ordering.
MIN_EVENT_GAP_SECONDS: Final[float] = 0.25

#: Fraction of searches that are a partial title rather than a full one.
PARTIAL_QUERY_SHARE: Final[float] = 0.58

#: Fraction of searches carrying a plausible typo.
TYPO_SHARE: Final[float] = 0.12

#: Fraction of searches that are a genre or generic word rather than a title.
GENERIC_QUERY_SHARE: Final[float] = 0.19

#: Generic search terms people actually type into a streaming app.
GENERIC_QUERIES: Final[tuple[str, ...]] = (
    "new releases",
    "comedy",
    "thriller",
    "action movies",
    "korean",
    "anime",
    "documentary",
    "hindi movies",
    "top 10",
    "kids",
    "horror",
    "romance",
    "stand up",
    "true crime",
    "sports",
    "sci fi",
    "trending",
    "originals",
)


@dataclass(slots=True)
class EventRow:
    """One ``core.events`` row.

    Field order matches :data:`seeder.loaders.EVENT_COLUMNS`. ``event_id`` is
    omitted: the column is ``BIGSERIAL`` and the loader lets PostgreSQL assign it,
    which avoids the seeder maintaining a global counter across batches.

    Attributes:
        session_id: Owning session.
        user_id: Owning user, denormalised so per-user scans need no join.
        content_id: Title, or ``None`` for navigation events.
        event_time: UTC timestamp, strictly increasing within a session.
        event_name: A ``core.event_name`` label.
        screen: Screen the event occurred on.
        step_index: Zero-based position within the session.
        watch_seconds: Incremental playback seconds, or ``None``.
        progress_pct: Cumulative playback percentage, or ``None``.
        properties: JSON payload.
    """

    session_id: int
    user_id: int
    content_id: int | None
    event_time: datetime
    event_name: str
    screen: str
    step_index: int
    watch_seconds: int | None
    progress_pct: float | None
    properties: dict[str, object]

    def as_row(self) -> tuple[object, ...]:
        """Render as a tuple for binary ``COPY``.

        Returns:
            Values in :data:`seeder.loaders.EVENT_COLUMNS` order. ``properties`` is
            passed as a mapping; :mod:`seeder.loaders` handles JSONB encoding.
        """
        return (
            self.session_id,
            self.user_id,
            self.content_id,
            self.event_time,
            self.event_name,
            self.screen,
            self.step_index,
            self.watch_seconds,
            self.progress_pct,
            self.properties,
        )


class ContentSelector:
    """Selects titles for a user, weighted by taste and availability.

    Holds the catalogue and a per-genre index. Constructed once per seed run and
    shared across every user, because rebuilding the weight arrays per session
    would dominate generation time.
    """

    __slots__ = ("_added_ordinals", "_catalogue", "_genre_of", "_titles")

    def __init__(self, catalogue: list[ContentRow], genre_names: dict[int, str]) -> None:
        """Initialise the selector.

        Args:
            catalogue: Every generated title.
            genre_names: ``genre_id`` to genre name, for affinity lookup.
        """
        self._catalogue = catalogue
        self._genre_of = {row.content_id: genre_names[row.genre_id] for row in catalogue}
        self._titles = {row.content_id: row.title for row in catalogue}
        # Ordinal dates precomputed: availability is checked for every candidate on
        # every slot, so a date subtraction per check is measurable at this volume.
        self._added_ordinals = {row.content_id: row.added_on.toordinal() for row in catalogue}

    def available_on(self, day: date) -> list[ContentRow]:
        """Return titles already in the catalogue on a date.

        Args:
            day: The session date.

        Returns:
            Available titles.
        """
        ordinal = day.toordinal()
        return [
            row for row in self._catalogue if self._added_ordinals[row.content_id] <= ordinal
        ]

    def choose(
        self,
        rng: Random,
        *,
        profile: UserProfile,
        days_since_signup: int,
        day: date,
        count: int,
        candidates: list[ContentRow] | None = None,
    ) -> list[ContentRow]:
        """Choose distinct titles for one session.

        Args:
            rng: Seeded random source.
            profile: The user's realised profile.
            days_since_signup: Selects pre- or post-graduation behaviour.
            day: The session date, for availability and recency.
            count: How many distinct titles to return.
            candidates: Pre-filtered availability list. Supplied by the timeline
                walk, which computes it once per day rather than once per session.

        Returns:
            Up to ``count`` distinct titles, possibly fewer if the catalogue is
            smaller than requested.
        """
        pool = candidates if candidates is not None else self.available_on(day)
        if not pool:
            return []

        ordinal = day.toordinal()
        weights = [
            content_weight(
                profile,
                days_since_signup=days_since_signup,
                genre=self._genre_of[row.content_id],
                content_type=row.content_type,
                popularity=float(row.popularity_score),
                days_since_added=ordinal - self._added_ordinals[row.content_id],
            )
            for row in pool
        ]

        total = sum(weights)
        if total <= 0.0:
            # Every candidate scored zero, which happens only if a persona's
            # affinities exclude the entire available catalogue. Fall back to
            # uniform rather than returning nothing.
            weights = [1.0] * len(pool)

        chosen: list[ContentRow] = []
        remaining = list(range(len(pool)))
        remaining_weights = list(weights)

        for _ in range(min(count, len(pool))):
            pick = rng.choices(range(len(remaining)), weights=remaining_weights, k=1)[0]
            chosen.append(pool[remaining[pick]])
            remaining.pop(pick)
            remaining_weights.pop(pick)
            if not remaining:
                break

        return chosen

    def title_of(self, content_id: int) -> str:
        """Return a title's display name.

        Args:
            content_id: The title.

        Returns:
            The title string.
        """
        return self._titles[content_id]


def _corrupt(rng: Random, text: str) -> str:
    """Introduce a plausible typo.

    Args:
        rng: Seeded random source.
        text: The query string.

    Returns:
        The string with one character transposed, dropped or doubled.
    """
    if len(text) < 4:
        return text

    position = rng.randrange(1, len(text) - 1)
    mode = rng.choice(("transpose", "drop", "double"))

    if mode == "transpose":
        chars = list(text)
        chars[position], chars[position + 1] = chars[position + 1], chars[position]
        return "".join(chars)
    if mode == "drop":
        return text[:position] + text[position + 1 :]
    return text[:position] + text[position] + text[position:]


def make_search_terms(
    rng: Random,
    selector: ContentSelector,
    *,
    titles: list[ContentRow],
    count: int,
) -> list[str]:
    """Build realistic search queries.

    A real search log is mostly partial titles in lowercase, with a meaningful
    share of generic terms and a small share of typos. Reproducing that shape is
    what makes the top-searches list on the Content page look like a search log
    rather than a column of identical placeholders.

    Args:
        rng: Seeded random source.
        selector: Used to resolve title text.
        titles: Titles this session will engage with, so searches are correlated
            with what the user actually goes on to watch.
        count: How many terms to produce.

    Returns:
        Query strings, possibly fewer than ``count`` if there is nothing to draw
        from.
    """
    terms: list[str] = []

    for _ in range(count):
        if not titles or rng.random() < GENERIC_QUERY_SHARE:
            terms.append(rng.choice(GENERIC_QUERIES))
            continue

        title = rng.choice(titles).title

        # Strip a sequel suffix: people search the base title, not "Foo: Part Two".
        base = title.split(":")[0].strip()
        query = base.lower()

        if rng.random() < PARTIAL_QUERY_SHARE:
            words = query.split()
            if len(words) > 1:
                keep = rng.randint(1, len(words))
                query = " ".join(words[:keep])
            elif len(query) > 5:
                query = query[: rng.randint(3, len(query) - 1)]

        if rng.random() < TYPO_SHARE:
            query = _corrupt(rng, query)

        terms.append(query)

    return terms


def materialise(
    plan: SessionPlan,
    *,
    session_id: int,
    user_id: int,
    started_at: datetime,
    slot_content_ids: list[int],
    ceiling: datetime,
) -> tuple[list[EventRow], datetime]:
    """Convert a plan into timestamped event rows.

    Args:
        plan: The planned session.
        session_id: Owning session id.
        user_id: Owning user id.
        started_at: UTC timestamp of the first event.
        slot_content_ids: One ``content_id`` per content slot, in slot order.
        ceiling: Hard upper bound on any event time, normally the window end.

    Returns:
        ``(rows, ended_at)`` — the event rows and the final event's timestamp,
        which becomes ``core.sessions.session_end``.
    """
    total_dwell = sum(event.dwell_seconds for event in plan.events)

    # Compress rather than truncate when the session would overrun the ceiling.
    # Dropping the tail would bias every duration percentile downward for sessions
    # near the end of the window, and that bias would be invisible in the output.
    scale = 1.0
    available = (ceiling - started_at).total_seconds()
    if total_dwell > 0 and available > 0 and total_dwell > available:
        scale = max(available / total_dwell, 0.01)

    rows: list[EventRow] = []
    cursor = started_at
    last_time = started_at - timedelta(seconds=MIN_EVENT_GAP_SECONDS)

    for step_index, event in enumerate(plan.events):
        cursor = cursor + timedelta(seconds=event.dwell_seconds * scale)

        # Strictly increasing, so step_index and event_time never disagree.
        if cursor <= last_time:
            cursor = last_time + timedelta(seconds=MIN_EVENT_GAP_SECONDS)
        if cursor > ceiling:
            cursor = ceiling
        last_time = cursor

        content_id: int | None = None
        if event.slot is not None and event.slot < len(slot_content_ids):
            content_id = slot_content_ids[event.slot]

        # Playback seconds scale with the session so watch_seconds cannot exceed
        # duration_seconds after compression, which ck_sessions_watch_within_
        # duration would otherwise reject.
        watch_seconds = event.watch_seconds
        if watch_seconds is not None and scale < 1.0:
            watch_seconds = max(0, int(watch_seconds * scale))

        rows.append(
            EventRow(
                session_id=session_id,
                user_id=user_id,
                content_id=content_id,
                event_time=cursor,
                event_name=event.event_name,
                screen=event.screen,
                step_index=step_index,
                watch_seconds=watch_seconds,
                progress_pct=event.progress_pct,
                properties=dict(event.properties),
            )
        )

    return rows, cursor


def watch_seconds_of(rows: list[EventRow]) -> int:
    """Sum incremental playback seconds across event rows.

    Recomputed from the materialised rows rather than taken from the plan, because
    compression may have scaled them. This is the value written to
    ``core.sessions.watch_seconds``, and ``tests/test_seeder.py`` asserts the two
    agree.

    Args:
        rows: Event rows for one session.

    Returns:
        Total playback seconds.
    """
    return sum(row.watch_seconds or 0 for row in rows)


__all__ = [
    "GENERIC_QUERIES",
    "MIN_EVENT_GAP_SECONDS",
    "ContentSelector",
    "EventRow",
    "make_search_terms",
    "materialise",
    "watch_seconds_of",
]
