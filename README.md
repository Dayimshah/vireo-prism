# Prism

**A product analytics platform for a streaming service that doesn't exist.**

## Live Demo

**[https://vireo-prism.vercel.app](https://vireo-prism.vercel.app)**

> The backend runs on a free-tier server and sleeps after inactivity. First load takes ~30–60 seconds to wake up — after that it's fast.

| Layer | Live URL |
|-------|----------|
| Dashboard | [vireo-prism.vercel.app](https://vireo-prism.vercel.app) |
| API | [vireo-prism-api.onrender.com/api/v1](https://vireo-prism-api.onrender.com/api/v1/meta/bounds) |

---

## At a Glance

| | |
|---|---|
| **Stack** | Python 3.12, FastAPI, PostgreSQL 16, React 18, TypeScript, Tailwind |
| **Data** | 1.1M events, 600 users, 18 months of synthetic behaviour |
| **SQL** | 51 hand-written analytical queries (window functions, percentiles, cohorts) |
| **API** | 55 endpoints, RFC 7807 errors, caching, rate limiting |
| **Frontend** | 11 dashboard pages, 8-dimension filtering, dark mode |
| **Tests** | 330 — testing relationships and invariants, not hardcoded numbers |
| **Infra** | Docker Compose (local), Neon + Render + Vercel (production) |

---

Think of the person at Netflix or Hotstar whose job is to answer "why did revenue
dip last month?" or "which marketing channel is actually worth the spend?" They
don't guess. They open a dashboard built on top of millions of rows of user
behaviour, and the dashboard has to be right, because someone is about to spend
real money based on it.

Prism is that dashboard, and the warehouse under it, and the API in between. Vireo
is the fictional streaming service it reports on.

---

## The part I actually care about

Anyone can generate random data and chart it. Random data produces flat lines and
identical segments, and it takes a reviewer about four seconds to notice that every
bar is the same height.

So I did something different. **The data generator plants deliberate causes, and
the analytics layer is never told what they are.**

The generator knows that Referral traffic brings users who pay and Display traffic
doesn't. It knows a Binge Watcher churns at a tenth the rate of a Churn Risk. It
knows watch time drives conversion through a logistic curve. Then it simulates
eighteen months of ordinary behaviour — people opening the app, browsing, watching,
abandoning halfway, subscribing, cancelling.

The 51 SQL queries that power the dashboard know **none** of that. They just
aggregate events. So when the Marketing page shows Referral outperforming Display,
that number wasn't looked up anywhere — it was *rediscovered* from the event
stream.

It's the analytics equivalent of hiding the answer key, then checking whether your
instruments can find it.

And here's the bit I'm proudest of: **sometimes they can't, and I wrote that down
too.** More on that below.

---

## What's in the box

```
React dashboard          11 pages, charts, filters, dark mode
       ↓  asks for JSON
FastAPI                  55 endpoints, caching, rate limits
       ↓  runs SQL
PostgreSQL               1.1 million events, 546 days of history
```

| Layer | What it does |
| --- | --- |
| **Warehouse** | 13 tables, 1.09M events split across 65 monthly partitions, 4 pre-computed views so the heavy joins happen once |
| **API** | 55 operations (54 reads, 1 write). Consistent response shape, proper error documents, caching, rate limiting |
| **SQL** | 51 hand-written query files. No ORM — window functions and percentiles are the whole point |
| **Dashboard** | 11 pages, 64 TypeScript modules, every chart filterable eight ways |
| **Generator** | Simulates people, not rows: per-personality navigation patterns, local-evening viewing habits, planted cause-and-effect |
| **Tests** | 330, and they check that the *numbers* are right, not just that nothing crashed |
| **Power BI** | Star schema + ~70 DAX measures for reading the same data in Power BI |

Built with Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy, React 18, TypeScript,
Vite, Tailwind, Recharts. Redis optional. Everything runs from one command.

---

## Try it

You need Docker. That's it.

```bash
make env          # set up config
make up           # start everything, run migrations
make seed-small   # generate 600 users and ~1.1M events (~2.5 min)
```

Then open:

| | |
| --- | --- |
| **Dashboard** | <http://localhost:5173> |
| **API docs** | <http://localhost:8010/docs> |

`make help` lists everything else.

Small heads-up: the API is on port **8010** and Postgres on **5433**, not the
usual 8000 and 5432. Something else on my machine had already claimed those, and
hardcoding a port that collides is a rude thing to do to whoever clones your repo.

### If you're poking at the API directly

The date filters have **no defaults**, on purpose. If you clone this six months
from now and I'd defaulted to "last 30 days", every chart would open empty and
you'd reasonably assume the thing was broken. So ask what data exists first:

```bash
curl -s http://localhost:8010/api/v1/meta/bounds | jq .data
```

```json
{
  "first_activity_date": "2025-02-07",
  "last_activity_date": "2026-08-06",
  "days": 546,
  "users": 600,
  "events": 1092554
}
```

Then pick a window inside that:

```bash
curl -s "http://localhost:8010/api/v1/kpi/dau?date_from=2026-07-01&date_to=2026-08-06" | jq
```

---

## Three things that will trip you up

**An empty value means "we don't know", never "zero".** If a day has no active
users, there's no average session length for that day — so the API returns `null`
and the dashboard draws an em-dash, not a zero. Line charts leave a visible gap
instead of dipping to the floor. A missing measurement and a measurement of zero
are different facts, and quietly turning the first into the second is how a
dashboard starts lying to you.

**Percentages come in two flavours.** Columns ending in `_pct` are already
multiplied (`51.1` means 51.1%). A few others are 0–1 fractions (`0.511`). Mix them
up and you're wrong by a factor of 100. Both conventions are documented per column,
because picking one and silently converting the other is how you end up shipping a
chart claiming 5,110% completion.

**A typo in a query parameter is a loud error, not a shrug.** Send `date_form`
instead of `date_from` and you get a 422. The tempting alternative — ignore what
you don't recognise — returns a cheerful `200 OK` full of numbers for the wrong
time window, which is so much worse than an error.

---

## Where it falls short

Every portfolio project has limitations. Most READMEs don't mention them. I think
that's backwards — a project you can't critique is a project you don't understand,
so here are the real ones, measured rather than hand-waved.

**The A/B tests can't prove anything at the default data size, and the statistics
correctly refuse to pretend otherwise.** Each experiment arm has 12–65 users.
Detecting the effects I planted (4–8 percentage points) needs 268–986 users per
arm. So every verdict comes back "underpowered" or "inconclusive", and two of the
four even land on the *wrong sign*.

That's not a bug — it's the honest output of a correct significance test on a small
sample. A worse version of this project would report those as findings. Seed the
`medium` or `large` profile and the effects appear.

**One metric is structurally broken, and I left it broken deliberately.** The
`day7_retention` experiment reads 0% across all three arms. Two individually
correct decisions collide: the query only counts activity *after* a user joins the
experiment (right, otherwise you measure who they already were), but the generator
enrols people anywhere from 0 to 315 days after signup. If you joined on day 9,
your day-7 activity is filtered out before it can count. Only 4 of 169 users can
possibly register.

Fixing it means redesigning either the metric or the enrolment timing. That's a
design decision, not a patch, so it's documented rather than papered over.

**Marketing channel rankings are directional, not a league table.** The correlation
between what I planted and what the SQL recovers is ρ = 0.71. The extremes come
back clean; the middle is noise. One channel has 7 users total — one coin flip from
looking like the best in the dataset.

**Only 4 of 8 designed experiments exist at the small size**, and because they're
taken in order, the deliberately-null and deliberately-negative ones are exactly
the ones that don't appear.

**Seven personas show up, not eight.** "New Explorer" graduates into another
personality after 30 days, because "new user" isn't a permanent identity — nobody
is still labelled *new* eighteen months in. A missing bar there is correct.

**The read endpoints have no authentication.** Every row is synthetic and the whole
point is that you can click through it. A login wall would defeat the purpose. Only
the one endpoint that rebuilds the heavy views is protected, and that's because
it's expensive enough to be abused, not because the data is precious.

**Old lint debt is reported, not hidden.** Roughly 100 style findings and 15 type
findings live in code I wrote in earlier phases. CI reports the count on every push
in a job that doesn't block the build. I could have mass-reformatted everything for
a green badge, but rewriting working reviewed code to satisfy a formatter is how you
introduce a real bug while chasing a cosmetic one. So the badge means "tests pass
and it builds" — and says so out loud.

---

## About the tests

330 of them, and the interesting ones don't check specific numbers.

Pinning a literal — `assert revenue == 4821.50` — proves almost nothing. You copied
the expected value from the code you're testing, and it breaks the moment anyone
regenerates the data.

So instead they assert **relationships that must hold no matter what the data
says**:

- multiply the funnel's step-by-step rates and you get the end-to-end rate
- the revenue waterfall balances: opening + all movements = closing
- a churn risk score equals the sum of its five components
- "rolling" retention can never be lower than "classic" retention
- conversion rises as watch time rises, every single time

Those survive a full data regeneration. They're also the assertions that would
actually catch a broken query.

---

## Documentation

Seven documents, all written by querying the live database rather than from memory —
which is how I caught four of my own wrong claims, including a table count I'd
gotten wrong in three separate files.

| Document | What's in it |
| --- | --- |
| [architecture.md](docs/architecture.md) | How the layers fit, why the SQL lives in files |
| [data-model.md](docs/data-model.md) | ER diagram, partitioning, why empty values mean something |
| [api.md](docs/api.md) | Every endpoint, filters, errors, rate limits |
| [analytics-catalog.md](docs/analytics-catalog.md) | The statistics, and what the data can and can't show |
| [decisions.md](docs/decisions.md) | Eleven trade-offs and what each one cost |
| [seeder-design.md](docs/seeder-design.md) | How the fake behaviour gets generated |
| [powerbi.md](docs/powerbi.md) | Star schema, relationships, DAX conventions |

### On the Power BI piece

There's no `.pbix` file here, and that's a deliberate choice rather than an
oversight. A `.pbix` is an opaque binary: you can't review it in a pull request, it
bakes in a connection string, and it's stale the moment the schema moves.

What's here instead is the part that carries the actual thinking — the star schema
as reviewable SQL and the measures as reviewable DAX, with the modelling traps
already solved (the year-boundary sort bug, the relationship that has to be
inactive, the measure that would silently read 0% forever). Point Power BI Desktop
at the schema, paste the measures in, and you're building visuals in a few minutes.

---

## Development

```bash
make test         # 330 tests
make check        # lint + types + tests
make report       # data-quality report: 12 invariant checks + 8 charts
make powerbi      # build the star schema
make fresh        # burn it all down and start over
```

Verified at the time of writing: 330 tests pass in 116 seconds, TypeScript
compiles clean across all 64 modules, ESLint passes, the production bundle builds
in 8 seconds.

---

## About me

I'm **Dayim Shah**, a Computer Science and Engineering graduate from **VIT Vellore**.

I built Prism because "I know SQL and React" is easy to say and hard to
demonstrate. What I wanted to show is the reasoning that sits between them: knowing
that a null and a zero are different claims about the world, that a significance
test which refuses to call a winner is doing its job, that partitioning a fact
table monthly instead of daily is a decision with a number attached to it.

The parts of this repo I'd point at first aren't the charts. They're
[decisions.md](docs/decisions.md), where every trade-off has its cost written next
to it, and the limitations section above — because knowing precisely where your
own work stops being trustworthy is most of the skill.

**Looking for roles in:** Data Engineering, Backend Development, Full-Stack Development, Analytics Engineering.

Happy to talk about any of it — reach me on [LinkedIn](https://www.linkedin.com/in/dayim-shah-612b03204) or [GitHub](https://github.com/Dayimshah).

---

<sub>Vireo is fictional. Every user, title, session and event is generated — no row
describes a real person, and no title is a real film or series.</sub>
