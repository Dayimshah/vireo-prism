# API reference

Base URL `http://localhost:8010/api/v1`. Interactive docs at `/docs`, schema at
`/openapi.json`.

55 operations: 54 GET and one POST. Everything is a read except
`POST /admin/refresh-analytics`.

## Before anything else: ask what the data covers

The window parameters have **no defaults**, deliberately. A "last 30 days" default
would open every chart empty on a repository cloned months after its dataset was
generated, and an empty chart reads as a broken service rather than as a badly
chosen window.

So the first call is:

```bash
curl -s http://localhost:8010/api/v1/meta/bounds | jq .data
```

```json
{
  "first_activity_date": "2025-02-07",
  "last_activity_date": "2026-08-06",
  "days": 546,
  "users": 600,
  "events": 1092554,
  "is_seeded": true
}
```

Then pick a window inside those bounds. `is_seeded` distinguishes a database that
has been migrated but never seeded from one with no activity — an important
difference, because the analytics materialized views raise on read when created
`WITH NO DATA` rather than returning empty results.

## The envelope

Every response but `/health` is `{data, meta}`:

```json
{
  "data": [{ "day": "2025-02-07", "dau": 3, "sessions": 5, "watch_seconds": 27613 }],
  "meta": {
    "cache": "MISS",
    "rows": 546,
    "window": { "date_from": "2025-02-07", "date_to": "2026-08-06", "days": 546 },
    "filters_applied": false,
    "generated_at": "2026-08-08T12:36:24.779767Z",
    "request_id": "bc0aaaa7183a4ebe9f3a564608c135ba"
  }
}
```

`data` is an array on 50 routes and an object on four: both `/meta` routes,
`/overview`, and the experiment results. `meta.rows` counts the array; for the
object payloads it is 1, except `/overview` where it counts the `tiles` array.

`request_id` also comes back as the `X-Request-ID` header and appears in every log
line for that request. Quote it in a bug report and the exact request is findable.

## Filters: uniform across 49 routes

Eight filter parameters, accepted by all 49 analytical routes and none of the other
five (`/health`, both `/meta` routes, `/search`, `/experiments`):

| Parameter | Type | Example |
| --- | --- | --- |
| `country` | repeated int | `?country=1&country=4` |
| `channel` | repeated int | `?channel=2` |
| `persona` | repeated int | `?persona=3` |
| `device` | repeated int | `?device=1` |
| `genre` | repeated int | `?genre=7` |
| `content_type` | repeated enum | `?content_type=movie` |
| `language` | repeated string | `?language=en` |
| `is_premium` | bool | `?is_premium=false` |

Valid values come from `GET /meta/filters`, which returns them grouped by
dimension. Omitting a filter means "all"; **an empty value is not the same thing** —
internally an empty sequence normalises to `NULL`, which the SQL reads as match-all,
because an empty array would match nothing and silently return zero rows for a
filter the caller believed was inactive.

`is_premium=false` is a real filter meaning "free users only", not an absent one.

## Windows are not uniform

This is the one part of the surface that requires attention, because
`strict_query` rejects an undeclared parameter with a **422** rather than ignoring
it. Sending `date_from` to a route that does not declare it is an error.

| Shape | Routes | Which |
| --- | --- | --- |
| `date_from` + `date_to` | 34 | KPI, sessions, content, funnel, monetization, marketing (2 of 3), events, geo/device-breakdown, churn/reason-mix, retention/resurrection, overview |
| `date_from` + `date_to` + `observation_end` | 10 | all cohort routes, retention (5 of 6), marketing/cac-payback |
| none | 7 | `/health`, both `/meta`, `/search`, `/experiments`, `/churn/risk-scorecard`, `/users/rfm-segments` |
| `date_to` only | 1 | `/geo/country-ranking` |
| `observation_end` only | 2 | both `/experiments/{key}/…` routes |

34 + 10 + 7 + 1 + 2 = 54.

Why each exception exists:

- **`observation_end`** is a maturity cutoff, distinct from the window. Day-90
  retention can only be computed for users who signed up at least 90 days before
  it; including newer users would put them in the denominator with no chance of
  appearing in the numerator, inventing a decay that is an artefact of the window.
- **`/geo/country-ranking`** ranks over all history up to a cutoff, so a lower
  bound is meaningless to it.
- **`/churn/risk-scorecard`** and **`/users/rfm-segments`** score users on their
  *current* state. A historical window has no meaning for either.

## Routes

### Overview

| Route | Window | Extra |
| --- | --- | --- |
| `GET /overview` | from+to | — |

Headline tiles with period-over-period deltas. `data` is an object; `data.tiles` is
an array of six.

### KPI

| Route | Window |
| --- | --- |
| `GET /kpi/dau` | from+to |
| `GET /kpi/wau` | from+to |
| `GET /kpi/mau` | from+to |
| `GET /kpi/stickiness` | from+to |
| `GET /kpi/new-vs-returning` | from+to |
| `GET /kpi/sessions-per-user` | from+to |

`wau` and `mau` are rolling 7- and 28-day windows, not calendar weeks and months.
`stickiness` is DAU/MAU as a percentage — scale-free, so it cannot be inflated by
acquisition alone.

### Retention

| Route | Window | Extra |
| --- | --- | --- |
| `GET /retention/nday` | from+to+obs | — |
| `GET /retention/rolling` | from+to+obs | — |
| `GET /retention/unbounded` | from+to+obs | — |
| `GET /retention/by-segment` | from+to+obs | `segment_by`, `min_cohort_size` |
| `GET /retention/curve-by-persona` | from+to+obs | `min_cohort_size` |
| `GET /retention/resurrection` | from+to | — |

Three definitions, plotted together, and the differences are large enough to matter:
at day 1 classic retention reads 40.5% and rolling reads 91.5% on the seeded data.
Quoting classic day-1 retention as "retention" understates it by more than half.

`segment_by` accepts `country`, `channel`, `persona`, `device`, `premium`.

### Cohorts

| Route | Window | Extra |
| --- | --- | --- |
| `GET /cohort/monthly-matrix` | from+to+obs | `max_months`, `min_cohort_size` |
| `GET /cohort/weekly-matrix` | from+to+obs | `max_weeks`, `min_cohort_size` |
| `GET /cohort/revenue-cumulative` | from+to+obs | `min_cohort_size` |
| `GET /cohort/ltv-by-channel` | from+to+obs | `min_cohort_size` |

### Funnel

| Route | Window | Extra |
| --- | --- | --- |
| `GET /funnel/discovery-to-watch` | from+to | — |
| `GET /funnel/signup-to-subscribe` | from+to | — |
| `GET /funnel/step-dropoff` | from+to | — |
| `GET /funnel/time-between-steps` | from+to | — |
| `GET /funnel/by-segment` | from+to | `segment_by`, `min_cohort_size` |

`segment_by` here accepts `country`, `channel`, `persona`, `form_factor`,
`platform`, `premium` — **not** the same vocabulary as retention's. The funnel
segments by the session's device, which carries both `form_factor` and `platform`
as separate attributes. Passing an unrecognised token does not error: it falls
through to a single `all` bucket.

### Sessions

| Route | Window |
| --- | --- |
| `GET /sessions/duration-percentiles` | from+to |
| `GET /sessions/depth` | from+to |
| `GET /sessions/events-per-session` | from+to |
| `GET /sessions/activity-heatmap` | from+to |
| `GET /sessions/entry-exit-screens` | from+to |
| `GET /sessions/device-switching` | from+to |

### Content

| Route | Window | Extra |
| --- | --- | --- |
| `GET /content/top-watch-time` | from+to | `limit` |
| `GET /content/completion-rate` | from+to | `min_starts`, `limit` |
| `GET /content/trailer-to-start` | from+to | `min_starts`, `limit` |
| `GET /content/shelf-life-decay` | from+to | — |
| `GET /content/genre-performance` | from+to | — |
| `GET /content/genre-affinity` | from+to | — |

### Monetization

| Route | Window | Extra |
| --- | --- | --- |
| `GET /monetization/arpu-trend` | from+to | — |
| `GET /monetization/mrr-movement` | from+to | — |
| `GET /monetization/trial-conversion` | from+to | `min_cohort_size` |
| `GET /monetization/conversion-by-watch-decile` | from+to | — |

`mrr-movement` returns a waterfall that balances: `opening + new + reactivation +
expansion + contraction + churn = closing`, with contraction and churn signed
negative so the bars sum. Movement columns are `NULL` when nothing moved that way
in a month — `reactivation_mrr` is null for every month at the `small` profile.
That is the one place in this API where null-as-zero is the correct reading.

### Marketing

| Route | Window | Extra |
| --- | --- | --- |
| `GET /marketing/channel-attribution` | from+to | `min_cohort_size` |
| `GET /marketing/ltv-to-cac` | from+to | `min_cohort_size` |
| `GET /marketing/cac-payback` | from+to+obs | `min_cohort_size` |

### Churn

| Route | Window | Extra |
| --- | --- | --- |
| `GET /churn/reason-mix` | from+to | — |
| `GET /churn/risk-scorecard` | none | `min_risk_score`, `limit` |

`risk_score` is the exact sum of five component columns (`recency_points`,
`frequency_points`, `engagement_points`, `volume_points`, `tenure_points`), all
returned so the UI can show *why* an account is at risk. Bands: critical ≥ 70,
high ≥ 50, medium ≥ 30, low below. `primary_driver` names the largest component.

### Geography and audience

| Route | Window | Extra |
| --- | --- | --- |
| `GET /geo/country-ranking` | **`date_to` only** | `min_cohort_size` |
| `GET /geo/device-breakdown` | from+to | — |
| `GET /users/rfm-segments` | none | — |
| `GET /events/distribution` | from+to | — |

### Experiments

| Route | Window | Extra |
| --- | --- | --- |
| `GET /experiments` | none | — |
| `GET /experiments/{key}/variants` | `observation_end` | — |
| `GET /experiments/{key}/results` | `observation_end` | `alpha` |

Four experiments in the dataset: `autoplay-preview-v2`,
`continue-watching-position`, `onboarding-genre-picker` (3 variants),
`paywall-copy-value-first`. An unknown key is a 404 problem document, not an empty
result — an empty variant list would be indistinguishable from an experiment that
ran with no participants.

`results` runs a two-proportion z-test per variant against control, with Wilson
confidence intervals and observed power. `alpha` defaults to 0.05.

### Search and meta

| Route | Notes |
| --- | --- |
| `GET /search` | `q` (min 2 chars), `limit`. Unions content, users and experiments; read `result_type` rather than inferring the kind from which columns are populated |
| `GET /meta/filters` | Valid values for all eight filters |
| `GET /meta/bounds` | Dataset coverage and `is_seeded` |
| `GET /health` | Unenveloped. Liveness plus `database_connected`, `schema_ready`, `analytics_ready`, `cache_backend` |

### Admin

| Route | Notes |
| --- | --- |
| `POST /admin/refresh-analytics` | Requires `X-API-Key`. `concurrent` defaults true |

The only mutating operation. It is authenticated not merely on principle:
`REFRESH MATERIALIZED VIEW` over 1.1M events is expensive, so an open endpoint
would also be a denial-of-service lever.

```bash
curl -X POST http://localhost:8010/api/v1/admin/refresh-analytics \
  -H "X-API-Key: $PRISM_API__ADMIN_KEY"
```

## Errors

RFC 7807 problem documents:

```json
{
  "type": "https://prism.vireo.dev/problems/validation-error",
  "title": "Validation error",
  "status": 422,
  "detail": "...",
  "instance": "/api/v1/kpi/dau",
  "request_id": "..."
}
```

| Status | When |
| --- | --- |
| 401 | Missing or wrong `X-API-Key` on the admin route |
| 404 | Unknown experiment key (problem document); unmatched path (`{"detail": "Not Found"}`) |
| 405 | Wrong verb on a real route |
| 422 | Bad, missing, inverted or **undeclared** query parameter |
| 429 | Over the rate limit; carries `Retry-After` |

An inverted window (`date_from` after `date_to`) is a 422 rather than an empty
result, because an empty chart reads as "no activity in this period" rather than as
a bad request.

## Rate limiting

Token bucket per client address: 60 tokens, refilling at 240/minute. Sized so one
dashboard page load — a dozen concurrent requests — cannot rate-limit itself.
`/health`, `/docs` and `/openapi.json` are exempt.

Per worker, not per cluster: the state is in-process. Under `--workers N` the
effective limit is N times the configured one. It bounds accidental load — a
runaway `useEffect`, a scraper, a forgotten load test — and is not a defence
against a distributed attacker.

## Caching

Cache-aside, reported in `meta.cache` and the `X-Cache` header as `HIT`, `MISS` or
`NONE`. The cache key includes the route, the window and every filter, so two
windows are two entries.

Redis when reachable, a bounded in-process LRU otherwise. Values round-trip
through a tagged codec so a `Decimal` stays a `Decimal` on a hit rather than
degrading to a string — `X-Cache: HIT` and `MISS` return identically-typed
payloads.
