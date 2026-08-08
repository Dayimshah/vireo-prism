# Seeder design

How 1.1M events of plausible streaming behaviour get generated, and which parts of
that are load-bearing rather than decorative.

Three places in the delivered code point here: `alembic/versions/0003` and
`app/db/models.py` for catalogue generation, and `seeder/seasonality.py` for the
timezone-reproducibility choice.

The guiding constraint: **the SQL in `app/sql/queries/` knows none of this.** The
seeder declares causes; the analytics layer recovers relationships by aggregating
events. When the Marketing page shows Referral outperforming Display, that is
rediscovery, not a lookup. Everything below exists to make that possible.

## Running it

```bash
python -m seeder                            # medium profile, from .env
python -m seeder --profile small --truncate
python -m seeder --seed 42 --validate
python -m seeder --profile large --no-refresh
```

| Flag | Effect |
| --- | --- |
| `--profile` | `small` \| `medium` \| `large`. Default from `PRISM_SEED__PROFILE` |
| `--seed` | Fixes the dataset exactly. Default `PRISM_SEED__RANDOM_SEED` |
| `--truncate` | Empties the seeded tables first. **Dimension tables are never touched** |
| `--validate` | Asserts journey invariants on sampled sessions (slower) |
| `--no-refresh` | Skips the analytics matview refresh |
| `--report` | Writes `docs/data_quality_report.html` after loading |
| `--window-end` | Last day of the simulation window, `YYYY-MM-DD`. Default today |

`--window-end` matters more than it looks: the window is anchored to *today* by
default, so two people seeding on different days get datasets covering different
spans. Pin it to reproduce someone else's figures.

## Profiles

| | `small` | `medium` | `large` |
| --- | --- | --- | --- |
| Users | 600 | 4,000 | 15,000 |
| Titles | 320 | 340 | 360 |
| Experiments | **4** | **6** | **8** |
| Declared sessions | ~69,000 | ~463,000 | ~1,737,000 |
| Declared events | ~1,140,000 | ~7,580,000 | ~28,400,000 |
| Declared runtime | 150 s | 800 s | 2,900 s |

Only `users` is a dial. Sessions and events are **emergent** — they follow from the
persona mix and how long each user survives before churning.

**Measured against declared, `small` profile:**

| | Declared | Measured |
| --- | --- | --- |
| Users | 600 | 600 |
| Titles | 320 | 320 |
| Sessions | ~69,000 | **52,798** |
| Events | ~1,140,000 | **1,092,554** |

Events land within 4% of the estimate; sessions come in 23% low. The estimate in
`config.py` evidently assumed fewer events per session than the generator actually
produces — measured, it is **20.7 events per session** and 1,821 events per user
against a claimed ~1,900. The figure that carries the scale claim is right; the
session estimate is optimistic and is recorded here as measured rather than
restated.

Why the counts are lower than a "25,000 subscriber" headline would suggest: each
user generates ~1,900 events over eighteen months, so 4,000 users already yields a
7.6M-row fact table. That is large enough for partition pruning, BRIN indexes and
the materialized views to be load-bearing, and "7.6 million events" is the more
impressive figure anyway.

**Experiment count is a profile field, and it matters.** Because
`experiments.py` takes `EXPERIMENT_SPECS[:count]` in declaration order, the specs
carrying the deliberate null results and the deliberate regression only appear at
`medium` and `large`. See `analytics-catalog.md` — at `small` a `loser` verdict is
unreachable by construction.

## Determinism

One `random.Random(seed)` is constructed in `__main__.py` and every downstream
generator draws from it. The same `--seed` reproduces a dataset exactly.

This is why `seasonality.py` uses **fixed whole/half-hour UTC offsets rather than
IANA zones**. `zoneinfo` would make the dataset depend on the tzdata version
installed on the generating machine, and a DST transition would silently shift an
hour of history between two contributors' runs. Reproducibility beats DST fidelity
for synthetic data, and the naive datetimes that result are deliberate — three
carry documented `# noqa: DTZ001`.

## The catalogue

352 titles across 16 genres, 22 per genre, no duplicates. Verified by counting the
pool directly.

**Hand-written, not combinatorial.** A generator ("The {adjective} {noun}") is
faster to write and immediately recognisable as filler — every title reads like the
others and the Content page becomes unreadable. The pool is authored per genre so
that a leaderboard showing *Karachi Nights*, *Quantum Drift* and *The Salt Road*
reads as a catalogue rather than as output.

Every title is fictional. No real film or series name appears, which keeps the
project clear of the licensing questions that come with scraping a real catalogue.

**Metadata is derived, not random.** Runtime, format, language, age rating and
popularity are conditioned on genre: anime is series-shaped with 24-minute
episodes, stand-up is a single 55–80-minute special, documentaries skew shorter
than features. So `SELECT genre, AVG(runtime_minutes) ... GROUP BY genre` returns
something a reader recognises as true, which a uniform draw over 1–400 minutes
would not.

**Popularity follows a Beta distribution**, so most titles are unremarkable and a
handful are hits. That long tail is what makes the content leaderboard interesting
and the popularity-vs-completion scatter meaningful; a uniform draw would flatten
the whole page.

**When the profile wants more titles than are authored.** `large` asks for 360
against a pool of 352, and proportional allocation leaves eight genres asked for 23
from 22. `_title_pool` appends a sequel or season marker — `Iron Monsoon: Part
Two` — which is both realistic for a streaming catalogue and honest, in that it
never invents a new name badly. Not a defect; a documented overflow path.

`(title, release_year)` is `UNIQUE` in `core.content`, and a collision nudges the
year down rather than renaming the title.

`build_catalog` asserts that `core.genres` covers every curated genre and raises
naming the missing ones, so a renamed genre in the migration fails loudly instead
of silently dropping 22 titles.

## Personas

Eight personas with three behavioural coefficients each
(`base_sessions_per_week`, `base_completion_rate`, `base_churn_propensity`),
declared in Alembic 0002 and read back by the seeder.

**`New Explorer` ends up with zero stored users, deliberately.** "Inside the first
30 days" is not a stable identity, so a New Explorer converts into one of the other
seven after `GRADUATION_DAYS = 30`, weighted by `GRADUATION_TARGETS` toward Casual
Viewer (0.37) — most new users of anything become light users. The stored
`persona_id` records where they *ended up*, which is what a real analyst sees:
nobody is labelled "new" eighteen months in. Their early-life behaviour still
reflects exploration, so day-1 and day-7 retention for those users genuinely
differs from their steady state.

Any query grouping by persona therefore returns **seven groups, not eight**.

Measured churn by persona is monotone in `base_churn_propensity` across all seven
populated personas, with one inversion inside noise — the table is in
`analytics-catalog.md`.

## The journey engine

`journeys.py` answers one question: in what order does a real person tap through a
streaming app? It returns an ordered, legal event sequence for one session;
`generators/events.py` assigns timestamps and content ids from that plan.

**Not one 15×15 transition matrix.** The obvious implementation is also wrong, and
instructively so: a raw matrix can emit `COMPLETE_VIDEO` without a preceding
`START_VIDEO`, or `RATE` for an abandoned title. Those violate invariants the
analytics layer depends on — the funnel would show more completions than starts —
and tuning the probabilities only makes the bug rarer and harder to find.

So the model splits in two:

- **Navigation** is a genuine Markov chain over six states (`OPEN_APP`, `HOME`,
  `BROWSE_GENRE`, `SEARCH`, `VIEW_CONTENT`, `EXIT`). This is where persona
  differences live: a Movie Lover arrives via `SEARCH` because they know the title;
  a Casual Viewer loops `HOME → BROWSE_GENRE → HOME` and often leaves without
  watching.
- **Playback** is an atomic block: `START_VIDEO → VIDEO_PROGRESS* → (PAUSE_VIDEO)
  → COMPLETE_VIDEO | ABANDON_VIDEO → (RATE)`. Illegal orderings here are not
  improbable, they are unrepresentable.

Guaranteed by construction, for every plan:

- opens with `OPEN_APP`, closes with `EXIT`
- `START_VIDEO` is always preceded by `VIEW_CONTENT` on the same content slot
- `COMPLETE_VIDEO` and `ABANDON_VIDEO` never both occur for one slot
- `RATE` occurs only after `COMPLETE_VIDEO` on the same slot
- `progress_pct` is non-decreasing within a slot and ends at or above
  `COMPLETION_THRESHOLD_PCT` exactly when the title completes
- `content_id` presence matches the `ck_events_content_id_presence` constraint

These are re-asserted against the loaded database, so the guarantee is verified
rather than intended.

## Temporal shape

Four effects multiply into one intensity that places sessions:

```
intensity = hour_weight(local_hour, weekday)
          * weekday_multiplier(weekday)
          * holiday_multiplier(local_date, country)
          * growth_multiplier(days_into_window)
```

Timestamps are drawn in the user's **local** time and stored as UTC, because people
watch television in their own evening. Measured, the peak UTC hour spans the whole
clock by country and collapses onto 20:00–21:00 local — the table is in
`decisions.md`, along with the consequence that `mv_user_daily` buckets by UTC date
while users behave on local dates.

## Statistical honesty

Two properties enforced rather than hoped for:

- **No lookahead.** A user's conversion probability at time *t* depends only on
  behaviour before *t*; features come from a 14-day trailing window, never from the
  future.
- **No degenerate certainty.** Every probability is bounded away from 0 and 1 by
  `PROBABILITY_FLOOR`. A deterministic outcome would let a model reach 100%
  accuracy, which is the signature of a leaked label.

`CONVERSION_INTERCEPT` is a cautionary tale worth keeping. An earlier value of
−4.20 was set as though the draw were one-shot, but it is evaluated daily: an
inert user's 1.48% daily chance compounded to 97.6% over 250 days, so users who
watched nothing converted anyway and the engagement signal was drowned entirely.
The seeded run showed 60.7% conversion, which is what exposed it. It is now −10.00.

Measured population conversion at `small` is 8.3% (50 payers of 600).

## Load order

Foreign keys force part of it and memory forces the rest.

`events` → `sessions` → `users`, so users must land first. But three `core.users`
columns (`is_premium`, `last_seen_at`, `churned_at`) are only known *after* the
timeline walk, and at `medium` the walk produces ~3.4M event rows that cannot all
be held as Python objects.

Resolution: **process users in chunks.** For each chunk the walk runs, then that
chunk's users, sessions, events and subscriptions are copied in dependency order,
then the objects are dropped. Peak memory stays bounded by one chunk regardless of
profile, and no table is written before its parent. The four tables are written
sequentially per chunk because a single connection can only have one `COPY` in
progress.

**The whole load is one transaction.** A failure at row three million leaves the
database exactly as it was, rather than half-populated in a way that looks like
real data.

`COPY` runs in text rather than binary format — the reasoning, which is about enum
OIDs, is in `decisions.md`. So is the sequence-reset step that `core.events`
deliberately skips.

Measured storage after a `small` seed: `core.events` is 311 MB across 65
partitions, 19 of them holding rows, with `events_default` empty as designed. The
four matviews total ~23 MB. Database 359 MB.

## Verifying the output

```bash
python -m seeder.report          # writes docs/data_quality_report.html
make report
```

Anyone reviewing a project built on synthetic data will ask whether the data is any
good or merely busy. The report answers it in two halves.

**Twelve invariant checks**, run as SQL against the loaded database and reported
pass/fail, with the exit code reflecting the result so CI can gate on it. They
verify the journey ordering guarantees, the denormalisation agreement between
`core.sessions` and `core.events`, the absence of future timestamps, and that no
row escaped into `core.events_default`. A red row is a real bug.

**Eight distribution charts**, showing the generated behaviour has the shape it was
configured to have — and in three cases that the planted signals are independently
recoverable. The conversion-by-channel chart is the important one: the SQL knows
nothing about `CONVERSION_CHANNEL_EFFECT` yet recovers its ordering.

The hour-of-day chart deliberately plots both UTC and local time. The UTC curve is
flatter, because the world does not watch television simultaneously. A single sharp
global UTC peak is the signature of a naively generated dataset, and the chart
exists so a reader can see this one is not.

## What the `small` profile cannot show

Worth knowing before reading a chart as a finding. At 600 users the strong planted
effects recover cleanly (watch-time-to-conversion is monotone across quintiles,
persona churn ordering holds) but the weak ones do not: channel ranking reaches
only ρ = 0.71, and all four experiments are underpowered or inconclusive with two
carrying the wrong sign. Arms of 12–65 users against a detection floor of 15–34
percentage points cannot resolve 4–8-point planted lifts.

Seed `medium` or `large` to see the experiment effects recovered. Full arithmetic
in `analytics-catalog.md`.
