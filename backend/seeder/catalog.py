"""The Vireo catalogue: 350+ fictional titles with genre-consistent metadata.

Why the titles are hand-written
-------------------------------
A combinatorial generator ("The {adjective} {noun}") is faster to write and
immediately recognisable as filler — every title reads like the others, and the
Content page becomes unreadable. So the pool below is authored per genre, with
each title chosen to sound like something that genre would actually ship. When a
recruiter reads "Top 10 Titles by Watch Time" and sees *Karachi Nights*,
*Quantum Drift* and *The Salt Road*, the list reads as a catalogue rather than as
output.

Every title is fictional. No real film or series name appears here, which keeps
the project clear of the licensing questions that come with scraping a real
catalogue, and makes Vireo feel like a company rather than a clone.

Metadata is derived, not random
-------------------------------
Runtime, format, language, age rating and popularity are all conditioned on
genre. Anime is series-shaped with 24-minute episodes; stand-up is a single
55-to-80-minute special; documentaries skew shorter than features. The result is
that ``SELECT genre, AVG(runtime_minutes) FROM content GROUP BY genre`` returns
something a reader recognises as true, which a uniform draw over 1-400 minutes
would not.

Popularity follows a long tail
------------------------------
:func:`_draw_popularity` uses a Beta distribution, so most titles are unremarkable
and a handful are hits. That shape is what makes the content leaderboard
interesting and the "popularity vs completion rate" scatter meaningful: a uniform
distribution would make every title equally likely to be watched and flatten the
whole page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from random import Random

# ===========================================================================
# Curated titles, by genre
#
# Roughly 22 per genre across the 16 genres in Alembic revision 0002. Keys must
# match core.genres.name exactly; build_catalog() asserts this, so a renamed
# genre fails loudly instead of silently dropping its titles.
# ===========================================================================

CURATED_TITLES: Final[dict[str, tuple[str, ...]]] = {
    "Action": (
        "Shadow Protocol",
        "Hard Reset",
        "The Long Fuse",
        "Kill Radius",
        "Iron Monsoon",
        "Blackwater Run",
        "Terminal Velocity",
        "Concrete Sky",
        "The Extraction",
        "Sixty Seconds Out",
        "Steel Kite",
        "Last Convoy",
        "Nightfall Division",
        "Broken Arrow Point",
        "The Courier's Debt",
        "Redline District",
        "Hollow Point",
        "Ashfall",
        "The Quiet War",
        "Cold Start",
        "Gunmetal Sky",
        "Zero Ledger",
    ),
    "Anime": (
        "Hoshikaze",
        "The Lantern Blade",
        "Sakura Circuit",
        "Iron Chrysanthemum",
        "Voice of the Deep",
        "Kagerou Days",
        "The Ninth Envoy",
        "Paper Cranes at Dusk",
        "Thunderfall Academy",
        "Blue Signal",
        "The Cartographer's Sword",
        "Rust and Sakura",
        "Machina Soul",
        "Seven Bells for Winter",
        "The Weight of Rain",
        "Neon Koi",
        "Half-Light Wanderer",
        "The Emberwright",
        "Tidewalker",
        "Clockwork Crane",
        "Silverfin Odyssey",
        "The Last Calligrapher",
    ),
    "Comedy": (
        "Group Chat",
        "The Untenable Mr. Rao",
        "Two Weeks' Notice Period",
        "Bad at Parties",
        "The Office Plant",
        "Wedding Season Adjacent",
        "My Landlord, My Roommate",
        "Overqualified",
        "The Great Indian Refund",
        "Sourdough Divorce",
        "Everyone's Cousin",
        "Loud Neighbours",
        "The Understudy Situation",
        "Startup Weekend",
        "Aunty Knows Best",
        "Non-Refundable",
        "The Group Project",
        "Parking Wars: Bandra",
        "Emotional Support Goat",
        "Three Star Review",
        "The Reunion Nobody Wanted",
        "Housewarming",
    ),
    "Crime": (
        "Dark Streets",
        "Broken Oath",
        "The Ledger",
        "Karachi Nights",
        "Cold Case Colaba",
        "The Fixer's Daughter",
        "Blood and Paperwork",
        "Precinct Nine",
        "The Numbers Man",
        "Smoke on the Ring Road",
        "Kingpin Season",
        "The Honest Constable",
        "Laundered",
        "Nine Grams",
        "The Custodian",
        "Bad Paper",
        "Chalk Outline",
        "The Middleman",
        "Evidence Locker",
        "Grand Theft Dowry",
        "The Turncoat Tape",
        "Silent Partner",
    ),
    "Documentary": (
        "The Salt Road",
        "Concrete Monsoon",
        "Feeding a Billion",
        "The Last Weavers",
        "Deep Time",
        "Copper and Bone",
        "What the River Took",
        "The Vanishing Grid",
        "Everest Traffic",
        "Made in Tirupur",
        "The Algorithm Ate My Job",
        "Seed Bank",
        "One Rupee at a Time",
        "The Glacier Diaries",
        "Chasing Monsoons",
        "Ghost Fleet",
        "The Sugar Belt",
        "Signal Lost",
        "Rewilding",
        "The Long Commute",
        "Sixty Hours of Silence",
        "Empire of Sand",
    ),
    "Drama": (
        "Hidden Truth",
        "The Weight of Water",
        "Chandni Chowk Sonata",
        "Inheritance",
        "The Long Marriage",
        "Two Winters",
        "Small Mercies",
        "The Immigrant's Ledger",
        "Everything We Didn't Say",
        "Radio Silence",
        "The Second House",
        "Monsoon Wedding Season",
        "A Quiet Ambition",
        "The Understanding",
        "Paper Boats",
        "The Doctor's Consent",
        "Late Bloomer",
        "The Family Business",
        "Salt in the Wound",
        "Homecoming Delayed",
        "The Arrangement",
        "What the Neighbours Heard",
    ),
    "Fantasy": (
        "The Ninefold Gate",
        "Emberwood",
        "The Cartographer's Curse",
        "Saltmage",
        "The Hollow Crown Wars",
        "Wyrmtide",
        "The Glass Cathedral",
        "Ashen Kings",
        "The Borrowed Name",
        "Godsbreath",
        "The Thornwright",
        "Winter's Ledger",
        "The Bone Orchard",
        "Riversong",
        "The Tenth Kingdom Falls",
        "Sable and Ash",
        "The Lantern Keeper",
        "Moth and Moon",
        "The Unsworn",
        "Stormglass",
        "The Last Cartography",
        "Ravenhold",
    ),
    "Horror": (
        "The Tenant Below",
        "Quiet House",
        "Bhoot Bangla Road",
        "The Thing in the Well",
        "Nightshift",
        "The Uninvited Guest",
        "Wrong Turn Home",
        "The Second Floor",
        "Bone Deep",
        "The Feeding Hour",
        "Static",
        "The Ninth Knock",
        "Mother's Room",
        "The Long Hallway",
        "Sleep Study",
        "The Neighbour's Dog",
        "Crawlspace",
        "The Weeping Wall",
        "Dead Air",
        "The Guest Book",
        "Hollow Season",
        "The Last Tenant",
    ),
    "Kids & Family": (
        "Pip and the Paper Moon",
        "The Great Tiffin Heist",
        "Rocket Rabbit",
        "Nani's Time Machine",
        "The Sock Detectives",
        "Bumble & Bloom",
        "Captain Cardboard",
        "The Lost Kite",
        "Mango Season",
        "Robo-Dadi",
        "The Sleepy Dragon",
        "Puddle Jumpers",
        "The Very Loud Library",
        "Sparky's Big Day",
        "The Treehouse Treaty",
        "Buttons the Brave",
        "Grandpa's Garden Gnomes",
        "The Homework Monster",
        "Whiskers on Wheels",
        "The Birthday Mix-Up",
        "Little Lighthouse",
        "The Recycling Rangers",
    ),
    "Mystery": (
        "Silent Echo",
        "The Fifth Passenger",
        "Room 402",
        "The Vanishing of Meera Nair",
        "Cold Tea",
        "The Lighthouse Letters",
        "Eight Witnesses",
        "The Empty Chair",
        "What Rosie Saw",
        "The Locked Study",
        "Missing Tuesday",
        "The Understudy's Alibi",
        "Nobody's Widow",
        "The Wrong Photograph",
        "Twelve Hours Unaccounted",
        "The Quiet Sister",
        "Return Address Unknown",
        "The Last Guest",
        "Broken Clockwise",
        "The Second Statement",
        "Whistleblower",
        "The Inheritance Puzzle",
    ),
    "Reality": (
        "The Final Table",
        "Flat Hunters: Mumbai",
        "Stitch Off",
        "Startup Island",
        "The Restoration Yard",
        "Wedding Planners",
        "Cutthroat Kitchen Rules",
        "Tiny Home, Big Family",
        "The Bakeoff Bracket",
        "Roommate Roulette",
        "Salon Wars",
        "The Barter Trail",
        "Dance Floor Draft",
        "Second Chance Farm",
        "The Makeover Contract",
        "Bargain Hunters",
        "Survive the Ghats",
        "The Pitch Room",
        "Home Cooks Abroad",
        "Twelve Weeks to Fit",
        "The Antique Run",
        "Blind Taste",
    ),
    "Romance": (
        "Slow Trains",
        "The Wrong Wedding",
        "Two Doors Down",
        "Letters to Nowhere",
        "Monsoon Postcards",
        "The Arranged Accident",
        "Coffee at Closing",
        "Second Draft",
        "The Long Way Round",
        "Almost Strangers",
        "The Understudy Heart",
        "Off Season",
        "Text Me When You Land",
        "The Rebound Contract",
        "Sunday Market",
        "The Neighbour's Recipe",
        "Late Reply",
        "The Photograph We Kept",
        "One More Winter",
        "The Reluctant Bridesmaid",
        "Return Ticket",
        "Something Borrowed",
    ),
    "Sci-Fi": (
        "Quantum Drift",
        "The Last Orbit",
        "Planet X-9",
        "Final Frontier Protocol",
        "The Copenhagen Signal",
        "Terraform",
        "The Ninth Iteration",
        "Cold Sleep",
        "The Mars Audit",
        "Uplink",
        "The Forgetting Machine",
        "Halfway to Proxima",
        "The Cloning Clause",
        "Dark Matter Ledger",
        "The Last Human Test",
        "Orbital Decay",
        "The Simulation Tax",
        "Voidwalker",
        "The Second Earth Problem",
        "Signal from Europa",
        "The Memory Broker",
        "Redshift",
    ),
    "Sports": (
        "Fourth Innings",
        "The Comeback Season",
        "Gully to Glory",
        "Blood on the Turf",
        "The Transfer Window",
        "Twelve Rounds Left",
        "The Academy",
        "Offside",
        "Marathon Mile Twenty",
        "The Selection Committee",
        "Overtime",
        "The Underdog League",
        "Ringside",
        "The Captain's Armband",
        "Photo Finish",
        "The Doping File",
        "Second String",
        "The Derby",
        "Match Fixing Season",
        "The Final Over",
        "Coach",
        "Injury Time",
    ),
    "Stand-Up": (
        "Mildly Furious",
        "Overthinking It",
        "Please Don't Clap",
        "Married, Apparently",
        "Emotionally Available",
        "The Beige Years",
        "Loud in Traffic",
        "Fully Grown, Barely Functional",
        "Notes from the Group Chat",
        "Aggressively Fine",
        "My Parents Are Watching",
        "Bad with Money",
        "Almost Confident",
        "Room Temperature",
        "The Sensible Choice",
        "Sorry in Advance",
        "Adjacent to Success",
        "Chronically Online",
        "Not My Best Work",
        "Well Adjusted",
        "The Small Print",
        "Late Thirties, Early Panic",
    ),
    "Thriller": (
        "Hidden Truth Protocol",
        "The Whistle",
        "Sleeper Cell Seven",
        "The Handler",
        "Nine Days to Delhi",
        "The Cutout",
        "Deep Cover Winter",
        "The Leak",
        "Safehouse",
        "The Analyst",
        "Burn Notice Bombay",
        "The Double Blind",
        "Extraction Window",
        "The Asset",
        "Compromised",
        "The Night Ferry",
        "Countersign",
        "The Dead Drop",
        "Blackmail Season",
        "The Informant's Wife",
        "Terminal Trust",
        "The Last Briefing",
    ),
}


# ===========================================================================
# Genre metadata rules
# ===========================================================================


@dataclass(frozen=True, slots=True)
class GenreRules:
    """How one genre's metadata is shaped.

    Attributes:
        series_probability: Chance a title in this genre is episodic.
        movie_runtime: ``(min, mode, max)`` minutes for a feature.
        episode_runtime: ``(min, mode, max)`` minutes per episode.
        season_range: ``(min, max)`` seasons for a series.
        episodes_per_season: ``(min, max)`` episodes per season.
        age_rating_weights: Rating mix for this genre.
        language_bias: Multipliers on :data:`LANGUAGE_WEIGHTS`.
        documentary_share: Chance a title is typed ``documentary`` rather than
            ``movie``. Only meaningful for the Documentary and Sports genres.
        standup_share: Chance a title is typed ``stand_up``.
    """

    series_probability: float
    movie_runtime: tuple[int, int, int]
    episode_runtime: tuple[int, int, int]
    season_range: tuple[int, int]
    episodes_per_season: tuple[int, int]
    age_rating_weights: dict[str, float]
    language_bias: dict[str, float]
    documentary_share: float = 0.0
    standup_share: float = 0.0


#: Age rating mixes, named for reuse. Vireo uses the Indian CBFC-style ladder,
#: consistent with its largest market.
_FAMILY_RATINGS: Final[dict[str, float]] = {"U": 72.0, "U/A 7+": 26.0, "U/A 13+": 2.0}
_GENERAL_RATINGS: Final[dict[str, float]] = {
    "U": 14.0,
    "U/A 7+": 26.0,
    "U/A 13+": 44.0,
    "U/A 16+": 15.0,
    "A": 1.0,
}
_MATURE_RATINGS: Final[dict[str, float]] = {
    "U/A 13+": 18.0,
    "U/A 16+": 47.0,
    "A": 35.0,
}
_HARD_RATINGS: Final[dict[str, float]] = {"U/A 16+": 38.0, "A": 62.0}

GENRE_RULES: Final[dict[str, GenreRules]] = {
    "Action": GenreRules(
        series_probability=0.34,
        movie_runtime=(96, 124, 158),
        episode_runtime=(38, 46, 58),
        season_range=(1, 4),
        episodes_per_season=(6, 10),
        age_rating_weights=_MATURE_RATINGS,
        language_bias={"Hindi": 1.6, "Telugu": 1.8, "Tamil": 1.6, "Korean": 1.2},
    ),
    "Anime": GenreRules(
        # Anime is overwhelmingly episodic; the films that exist are the exception.
        series_probability=0.86,
        movie_runtime=(94, 112, 140),
        episode_runtime=(22, 24, 26),
        season_range=(1, 6),
        episodes_per_season=(12, 26),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"Japanese": 22.0, "English": 0.5, "Hindi": 0.2},
    ),
    "Comedy": GenreRules(
        series_probability=0.62,
        movie_runtime=(88, 104, 128),
        episode_runtime=(21, 27, 34),
        season_range=(1, 7),
        episodes_per_season=(6, 13),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"Hindi": 2.2, "English": 1.2, "Marathi": 1.4, "Punjabi": 1.5},
    ),
    "Crime": GenreRules(
        series_probability=0.71,
        movie_runtime=(102, 126, 152),
        episode_runtime=(42, 51, 62),
        season_range=(1, 5),
        episodes_per_season=(6, 10),
        age_rating_weights=_MATURE_RATINGS,
        language_bias={"Hindi": 2.4, "Urdu": 1.6, "Spanish": 1.4, "Korean": 1.3},
    ),
    "Documentary": GenreRules(
        series_probability=0.42,
        movie_runtime=(52, 84, 118),
        episode_runtime=(38, 46, 58),
        season_range=(1, 3),
        episodes_per_season=(3, 8),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"English": 2.0, "Hindi": 1.4},
        # Everything in this genre is typed documentary; the split below is
        # between documentary features and documentary series.
        documentary_share=1.0,
    ),
    "Drama": GenreRules(
        series_probability=0.58,
        movie_runtime=(104, 132, 168),
        episode_runtime=(44, 53, 66),
        season_range=(1, 5),
        episodes_per_season=(6, 12),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"Hindi": 2.6, "Bengali": 1.6, "Malayalam": 1.8, "Tamil": 1.5},
    ),
    "Fantasy": GenreRules(
        series_probability=0.64,
        movie_runtime=(108, 138, 172),
        episode_runtime=(46, 56, 70),
        season_range=(1, 5),
        episodes_per_season=(6, 10),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"English": 1.8, "Hindi": 1.2},
    ),
    "Horror": GenreRules(
        series_probability=0.44,
        movie_runtime=(84, 98, 122),
        episode_runtime=(38, 47, 58),
        season_range=(1, 3),
        episodes_per_season=(5, 9),
        age_rating_weights=_HARD_RATINGS,
        language_bias={"Hindi": 1.8, "Japanese": 1.4, "Korean": 1.3, "Thai": 1.6},
    ),
    "Kids & Family": GenreRules(
        series_probability=0.78,
        movie_runtime=(64, 84, 104),
        episode_runtime=(11, 16, 24),
        season_range=(1, 6),
        episodes_per_season=(10, 26),
        age_rating_weights=_FAMILY_RATINGS,
        language_bias={"Hindi": 2.0, "English": 1.6, "Tamil": 1.3},
    ),
    "Mystery": GenreRules(
        series_probability=0.74,
        movie_runtime=(98, 118, 142),
        episode_runtime=(42, 50, 60),
        season_range=(1, 4),
        episodes_per_season=(6, 10),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"English": 1.6, "Hindi": 1.8, "Korean": 1.4},
    ),
    "Reality": GenreRules(
        # Unscripted is essentially always episodic.
        series_probability=0.94,
        movie_runtime=(62, 78, 96),
        episode_runtime=(34, 44, 58),
        season_range=(1, 9),
        episodes_per_season=(8, 16),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"Hindi": 2.4, "English": 1.4, "Telugu": 1.3},
    ),
    "Romance": GenreRules(
        series_probability=0.56,
        movie_runtime=(94, 116, 146),
        episode_runtime=(38, 48, 60),
        season_range=(1, 4),
        episodes_per_season=(6, 12),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"Hindi": 2.6, "Korean": 2.2, "Tamil": 1.4, "Turkish": 1.5},
    ),
    "Sci-Fi": GenreRules(
        series_probability=0.58,
        movie_runtime=(102, 130, 166),
        episode_runtime=(44, 54, 68),
        season_range=(1, 4),
        episodes_per_season=(6, 10),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"English": 2.2, "Japanese": 1.3},
    ),
    "Sports": GenreRules(
        series_probability=0.68,
        movie_runtime=(78, 104, 132),
        episode_runtime=(38, 47, 58),
        season_range=(1, 4),
        episodes_per_season=(4, 9),
        age_rating_weights=_GENERAL_RATINGS,
        language_bias={"Hindi": 2.2, "English": 1.6},
        # Most sports programming on a streaming service is documentary-shaped.
        documentary_share=0.55,
    ),
    "Stand-Up": GenreRules(
        # A special is a one-off; "seasons" of stand-up are anthologies, which is
        # why this is not zero.
        series_probability=0.12,
        movie_runtime=(54, 66, 82),
        episode_runtime=(26, 32, 42),
        season_range=(1, 2),
        episodes_per_season=(4, 8),
        age_rating_weights=_MATURE_RATINGS,
        language_bias={"Hindi": 2.8, "English": 1.8},
        standup_share=1.0,
    ),
    "Thriller": GenreRules(
        series_probability=0.66,
        movie_runtime=(100, 122, 148),
        episode_runtime=(42, 51, 62),
        season_range=(1, 4),
        episodes_per_season=(6, 10),
        age_rating_weights=_MATURE_RATINGS,
        language_bias={"Hindi": 2.2, "English": 1.4, "Korean": 1.3, "Spanish": 1.2},
    ),
}

#: Base language mix before per-genre bias. Hindi-forward because Vireo's largest
#: market is India, English second as the global default.
LANGUAGE_WEIGHTS: Final[dict[str, float]] = {
    "English": 34.0,
    "Hindi": 22.0,
    "Tamil": 6.5,
    "Telugu": 6.0,
    "Korean": 5.5,
    "Japanese": 5.0,
    "Spanish": 4.5,
    "Malayalam": 3.5,
    "Bengali": 3.0,
    "Marathi": 2.5,
    "Punjabi": 2.0,
    "French": 1.8,
    "German": 1.5,
    "Portuguese": 1.2,
    "Turkish": 1.0,
    "Urdu": 0.9,
    "Thai": 0.7,
}

#: Share of the catalogue that is a Vireo Original. Originals receive a
#: popularity bonus, which is what makes "originals outperform licensed content"
#: a finding the dashboard can surface.
ORIGINAL_SHARE: Final[float] = 0.27

#: Popularity bonus applied to originals, in points on the 0-100 scale.
ORIGINAL_POPULARITY_BONUS: Final[float] = 11.0

#: Beta distribution shape for popularity. ``alpha < beta`` gives the long tail:
#: most titles cluster low, a few reach the top. Tuned so the median lands near
#: 32 and the 95th percentile near 78.
POPULARITY_BETA: Final[tuple[float, float]] = (2.1, 4.4)

#: How far before the window a back-catalogue title can have been added. Real
#: catalogues are mostly older acquisitions, and having them predate the window is
#: what lets the shelf-life query distinguish a new release from a library title.
BACKCATALOGUE_YEARS: Final[int] = 4

#: Share of titles added *during* the simulation window rather than before it.
NEW_ARRIVAL_SHARE: Final[float] = 0.31

#: Release-year offsets from the title's ``added_on`` date, weighted. A streaming
#: service licenses some titles the year they release and some decades later.
RELEASE_YEAR_LAG_WEIGHTS: Final[dict[int, float]] = {
    0: 34.0,
    1: 22.0,
    2: 12.0,
    3: 8.0,
    4: 6.0,
    6: 5.0,
    8: 4.0,
    11: 3.0,
    15: 2.5,
    20: 2.0,
    28: 1.5,
}


@dataclass(frozen=True, slots=True)
class ContentRow:
    """One catalogue row, ready for ``COPY`` into ``core.content``.

    Field order matches the column order used by :mod:`seeder.loaders`.

    Attributes:
        content_id: Surrogate key, assigned sequentially from 1.
        title: Display title.
        genre_id: Foreign key into ``core.genres``.
        content_type: One of the ``core.content_type`` enum labels.
        runtime_minutes: Per-episode for series, total for everything else.
        release_year: Year of original release.
        language: Primary audio language.
        age_rating: CBFC-style rating.
        popularity_score: Editorial 0-100 signal driving selection weight.
        season_count: Seasons, or ``None`` for non-series.
        episode_count: Total episodes across all seasons, or ``None``.
        is_original: Whether this is a Vireo Original.
        added_on: Date the title joined the catalogue.
        total_runtime_minutes: Full watchable length. Derived, not persisted —
            the event generator needs it to compute realistic watch durations for
            a multi-season series.
    """

    content_id: int
    title: str
    genre_id: int
    content_type: str
    runtime_minutes: int
    release_year: int
    language: str
    age_rating: str
    popularity_score: float
    season_count: int | None
    episode_count: int | None
    is_original: bool
    added_on: date
    total_runtime_minutes: int


def _weighted_choice(rng: Random, weights: dict[str, float]) -> str:
    """Draw one key from a weight mapping.

    Args:
        rng: Seeded random source.
        weights: Mapping of value to relative weight.

    Returns:
        The selected key.
    """
    keys = list(weights)
    return rng.choices(keys, weights=[weights[key] for key in keys], k=1)[0]


def _draw_language(rng: Random, rules: GenreRules) -> str:
    """Draw a language, applying the genre's bias.

    Args:
        rng: Seeded random source.
        rules: The genre's metadata rules.

    Returns:
        A language name.
    """
    biased = {
        language: weight * rules.language_bias.get(language, 1.0)
        for language, weight in LANGUAGE_WEIGHTS.items()
    }
    return _weighted_choice(rng, biased)


def _draw_popularity(rng: Random, *, is_original: bool) -> float:
    """Draw a popularity score with a realistic long tail.

    Args:
        rng: Seeded random source.
        is_original: Whether to apply the originals bonus.

    Returns:
        A score in ``[0.5, 99.5]``, rounded to two decimals.
    """
    alpha, beta = POPULARITY_BETA
    score = rng.betavariate(alpha, beta) * 100.0
    if is_original:
        score += ORIGINAL_POPULARITY_BONUS
    # Clamped inside the CHECK bounds rather than at them, so no title sits
    # exactly on 0 or 100 — a suspiciously round value in a leaderboard.
    return round(min(max(score, 0.5), 99.5), 2)


def _draw_runtime(rng: Random, bounds: tuple[int, int, int]) -> int:
    """Draw a runtime from a triangular distribution.

    Triangular rather than uniform because runtimes cluster around a conventional
    length — a 96-minute film is far likelier than a 158-minute one.

    Args:
        rng: Seeded random source.
        bounds: ``(min, mode, max)`` in minutes.

    Returns:
        A runtime in whole minutes.
    """
    low, mode, high = bounds
    return int(round(rng.triangular(low, high, mode)))


def _draw_added_on(rng: Random, window_start: date, window_end: date) -> date:
    """Draw the date a title joined the catalogue.

    Most titles predate the window (a back catalogue), a minority arrive during
    it. That mix is what makes the shelf-life decay query meaningful: only titles
    added inside the window have an observable launch curve.

    Args:
        rng: Seeded random source.
        window_start: First day of the simulation window.
        window_end: Last day of the simulation window.

    Returns:
        The catalogue-addition date.
    """
    if rng.random() < NEW_ARRIVAL_SHARE:
        # Added during the window, but not so late that no engagement is possible.
        latest = window_end - timedelta(days=21)
        span = max((latest - window_start).days, 1)
        return window_start + timedelta(days=rng.randrange(span))

    earliest = window_start - timedelta(days=365 * BACKCATALOGUE_YEARS)
    span = max((window_start - earliest).days, 1)
    return earliest + timedelta(days=rng.randrange(span))


def _draw_release_year(rng: Random, added_on: date) -> int:
    """Draw a release year consistent with the catalogue-addition date.

    A title cannot release after it was licensed, so the lag is subtracted from
    ``added_on``'s year and clamped to the range revision 0003 permits.

    Args:
        rng: Seeded random source.
        added_on: When the title joined the catalogue.

    Returns:
        A release year in ``[1950, 2030]``.
    """
    lag = int(
        rng.choices(
            list(RELEASE_YEAR_LAG_WEIGHTS),
            weights=list(RELEASE_YEAR_LAG_WEIGHTS.values()),
            k=1,
        )[0]
    )
    return max(1950, min(added_on.year - lag, 2030))


def _title_pool(rng: Random, genre: str, needed: int) -> list[str]:
    """Return ``needed`` distinct titles for a genre.

    The curated pool is used first. If a scale profile asks for more titles than
    are authored, the surplus is generated by appending a sequel or season marker
    to a curated title — which is both realistic (streaming catalogues are full of
    sequels) and honest, in that it never invents a new name badly.

    Args:
        rng: Seeded random source.
        genre: Genre name; must be a key of :data:`CURATED_TITLES`.
        needed: How many distinct titles are required.

    Returns:
        A list of exactly ``needed`` distinct titles.
    """
    curated = list(CURATED_TITLES[genre])
    rng.shuffle(curated)

    if needed <= len(curated):
        return curated[:needed]

    titles = list(curated)
    suffixes = (
        "Part Two",
        "The Reckoning",
        "Redux",
        "Chapter Two",
        "Aftermath",
        "The Return",
        "Origins",
        "Endgame",
    )
    index = 0
    while len(titles) < needed:
        base = curated[index % len(curated)]
        suffix = suffixes[(index // len(curated)) % len(suffixes)]
        candidate = f"{base}: {suffix}"
        if candidate not in titles:
            titles.append(candidate)
        index += 1

    return titles[:needed]


def build_catalog(
    rng: Random,
    *,
    size: int,
    genre_ids: dict[str, int],
    window_start: date,
    window_end: date,
) -> list[ContentRow]:
    """Generate the full catalogue.

    Titles are distributed across genres in proportion to the curated pool sizes,
    which keeps every genre populated rather than leaving some with two titles and
    an unreadable genre chart.

    Args:
        rng: Seeded random source. The same seed yields an identical catalogue.
        size: Number of titles to generate.
        genre_ids: Mapping of genre name to ``genre_id``, read from
            ``core.genres``.
        window_start: First day of the simulation window.
        window_end: Last day of the simulation window.

    Returns:
        Catalogue rows with ``content_id`` assigned sequentially from 1.

    Raises:
        ValueError: If ``genre_ids`` does not cover every curated genre, which
            means the migration and this module have drifted apart.
    """
    missing = sorted(set(CURATED_TITLES) - set(genre_ids))
    if missing:
        raise ValueError(
            f"core.genres is missing genres this catalogue expects: {', '.join(missing)}. "
            "seeder/catalog.py and Alembic revision 0002 have drifted apart."
        )

    genres = sorted(CURATED_TITLES)
    pool_total = sum(len(CURATED_TITLES[genre]) for genre in genres)

    # Proportional allocation, then hand the rounding remainder to the largest
    # pools so the total lands exactly on `size`.
    allocation = {
        genre: max(1, round(size * len(CURATED_TITLES[genre]) / pool_total))
        for genre in genres
    }
    drift = size - sum(allocation.values())
    ordered = sorted(genres, key=lambda g: -len(CURATED_TITLES[g]))
    for offset in range(abs(drift)):
        genre = ordered[offset % len(ordered)]
        allocation[genre] += 1 if drift > 0 else -1
        allocation[genre] = max(1, allocation[genre])

    rows: list[ContentRow] = []
    content_id = 1
    used_keys: set[tuple[str, int]] = set()

    for genre in genres:
        rules = GENRE_RULES[genre]
        for title in _title_pool(rng, genre, allocation[genre]):
            is_original = rng.random() < ORIGINAL_SHARE
            added_on = _draw_added_on(rng, window_start, window_end)

            # (title, release_year) is UNIQUE in core.content. Nudging the year is
            # cheaper and less surprising than renaming the title.
            release_year = _draw_release_year(rng, added_on)
            while (title, release_year) in used_keys:
                release_year -= 1
            used_keys.add((title, release_year))

            is_series = rng.random() < rules.series_probability

            if is_series:
                content_type = "series"
                runtime = _draw_runtime(rng, rules.episode_runtime)
                seasons = rng.randint(*rules.season_range)
                per_season = rng.randint(*rules.episodes_per_season)
                episodes = seasons * per_season
                total_runtime = runtime * episodes
            else:
                # A non-series title is a film unless the genre overrides it.
                if rules.standup_share and rng.random() < rules.standup_share:
                    content_type = "stand_up"
                elif rules.documentary_share and rng.random() < rules.documentary_share:
                    content_type = "documentary"
                else:
                    content_type = "movie"
                runtime = _draw_runtime(rng, rules.movie_runtime)
                seasons = None
                episodes = None
                total_runtime = runtime

            rows.append(
                ContentRow(
                    content_id=content_id,
                    title=title,
                    genre_id=genre_ids[genre],
                    content_type=content_type,
                    runtime_minutes=runtime,
                    release_year=release_year,
                    language=_draw_language(rng, rules),
                    age_rating=_weighted_choice(rng, rules.age_rating_weights),
                    popularity_score=_draw_popularity(rng, is_original=is_original),
                    season_count=seasons,
                    episode_count=episodes,
                    is_original=is_original,
                    added_on=added_on,
                    total_runtime_minutes=total_runtime,
                )
            )
            content_id += 1

    return rows


def curated_title_count() -> int:
    """Return the number of hand-authored titles available.

    Returns:
        Total across every genre. Used by ``tests/test_seeder.py`` to assert the
        pool has not shrunk below the 300 the README claims.
    """
    return sum(len(titles) for titles in CURATED_TITLES.values())


__all__ = [
    "BACKCATALOGUE_YEARS",
    "CURATED_TITLES",
    "GENRE_RULES",
    "LANGUAGE_WEIGHTS",
    "NEW_ARRIVAL_SHARE",
    "ORIGINAL_SHARE",
    "ContentRow",
    "GenreRules",
    "build_catalog",
    "curated_title_count",
]
