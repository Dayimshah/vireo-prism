import {
  Beaker,
  FlaskConical,
  Gauge,
  Globe2,
  Layers,
  LineChart,
  PlayCircle,
  Repeat,
  Users,
  Wallet,
  Filter as FunnelIcon,
  type LucideIcon,
} from 'lucide-react'

import type { ApiPath } from '@/api/endpoints'

/**
 * Every page in the app, in the order it appears in the sidebar.
 *
 * One table, two consumers: the sidebar renders it and the router builds its routes from
 * it. A page added here appears in both, and a page cannot exist in one and not the other —
 * which is the failure that produces either a dead nav link or a route no reader can find.
 *
 * The order mirrors `app/routers/__init__.py`
 * ------------------------------------------
 * That module hand-writes its mount order because it is the tag order in `/docs`, and it
 * runs as a reader would explore the product: the landing tiles, then engagement, then what
 * people watched, then the funnels and cohorts behind conversion, then money, then the
 * specialised lookups. Keeping the sidebar in the same sequence means the API docs and the
 * dashboard introduce the same product in the same order.
 *
 * Eleven pages, not ten
 * --------------------
 * The phase plan said ten. Splitting Marketing from Monetization is the eleventh: the two
 * share no endpoint, and channel attribution answers "where should the next pound go"
 * while ARPU answers "what is a subscriber worth" — different questions for different
 * readers. Declared here rather than absorbed silently, since the plan is the contract.
 *
 * `endpoints` is documentation, not wiring
 * ---------------------------------------
 * It records which of the 54 routes each page is responsible for, so phase 11 can be
 * checked for coverage rather than assumed complete. Nothing reads it at runtime.
 */
export interface RouteEntry {
  /** URL path, relative to the app root. */
  path: string

  /** Sidebar label. */
  label: string

  /** One line on what the page answers. Shown as the page subtitle. */
  description: string

  icon: LucideIcon

  /**
   * API paths this page is built from — the phase 11 coverage checklist.
   *
   * Typed as `ApiPath` rather than `string` so the compiler checks the checklist against the
   * generated schema. A mistyped or renamed path fails the build here instead of producing a
   * list that reads as authoritative and is quietly wrong — which is what makes the coverage
   * reconciliation (54 GET routes = 50 page-owned + 4 non-page) worth trusting.
   */
  endpoints: readonly ApiPath[]
}

export const ROUTES: readonly RouteEntry[] = [
  {
    path: '/',
    label: 'Overview',
    description: 'Headline figures for the window, against the period before it.',
    icon: Gauge,
    endpoints: ['/overview'],
  },
  {
    path: '/engagement',
    label: 'Engagement',
    description: 'Active users, stickiness, and how the event mix is composed.',
    icon: LineChart,
    endpoints: [
      '/kpi/dau',
      '/kpi/wau',
      '/kpi/mau',
      '/kpi/stickiness',
      '/kpi/new-vs-returning',
      '/kpi/sessions-per-user',
      '/events/distribution',
    ],
  },
  {
    path: '/retention',
    label: 'Retention',
    description: 'Who comes back, measured three ways that do not nest inside each other.',
    icon: Repeat,
    endpoints: [
      '/retention/nday',
      '/retention/rolling',
      '/retention/unbounded',
      '/retention/by-segment',
      '/retention/curve-by-persona',
      '/retention/resurrection',
    ],
  },
  {
    path: '/sessions',
    label: 'Sessions',
    description: 'Session length, depth, entry and exit points, and when people watch.',
    icon: PlayCircle,
    endpoints: [
      '/sessions/duration-percentiles',
      '/sessions/depth',
      '/sessions/events-per-session',
      '/sessions/entry-exit-screens',
      '/sessions/device-switching',
      '/sessions/activity-heatmap',
    ],
  },
  {
    path: '/content',
    label: 'Content',
    description: 'What gets watched, what gets finished, and how fast a title decays.',
    icon: Layers,
    endpoints: [
      '/content/top-watch-time',
      '/content/completion-rate',
      '/content/trailer-to-start',
      '/content/shelf-life-decay',
      '/content/genre-performance',
      '/content/genre-affinity',
    ],
  },
  {
    path: '/funnels',
    label: 'Funnels',
    description: 'Where sessions and signups fall out, and how long each step takes.',
    icon: FunnelIcon,
    endpoints: [
      '/funnel/discovery-to-watch',
      '/funnel/signup-to-subscribe',
      '/funnel/step-dropoff',
      '/funnel/time-between-steps',
      '/funnel/by-segment',
    ],
  },
  {
    path: '/cohorts',
    label: 'Cohorts',
    description: 'Monthly and weekly retention matrices, and revenue accumulated per cohort.',
    icon: Beaker,
    endpoints: [
      '/cohort/monthly-matrix',
      '/cohort/weekly-matrix',
      '/cohort/revenue-cumulative',
      '/cohort/ltv-by-channel',
    ],
  },
  {
    path: '/monetization',
    label: 'Monetization',
    description: 'ARPU, MRR movement, and what watching predicts about paying.',
    icon: Wallet,
    endpoints: [
      '/monetization/arpu-trend',
      '/monetization/mrr-movement',
      '/monetization/conversion-by-watch-decile',
      '/monetization/trial-conversion',
    ],
  },
  {
    path: '/marketing',
    label: 'Marketing',
    description: 'Channel attribution, LTV against CAC, and how long payback takes.',
    icon: Globe2,
    endpoints: ['/marketing/channel-attribution', '/marketing/ltv-to-cac', '/marketing/cac-payback'],
  },
  {
    path: '/audience',
    label: 'Audience',
    description: 'Churn reasons and risk, RFM segments, and where people are.',
    icon: Users,
    endpoints: [
      '/churn/reason-mix',
      '/churn/risk-scorecard',
      '/users/rfm-segments',
      '/geo/country-ranking',
      '/geo/device-breakdown',
    ],
  },
  {
    path: '/experiments',
    label: 'Experiments',
    description: 'Variant metrics and significance, with the multiple-comparison caveat stated.',
    icon: FlaskConical,
    endpoints: [
      '/experiments',
      '/experiments/{experiment_key}/variants',
      '/experiments/{experiment_key}/results',
    ],
  },
] as const

/**
 * The endpoints deliberately not owned by any page.
 *
 * Recorded so the phase 11 coverage check can reconcile to 54 rather than reporting four
 * apparent gaps:
 *
 * * `/search` is the topbar's, not a page's.
 * * `/meta/filters` and `/meta/bounds` feed the filter bar and the window picker.
 * * `/health` is the shell's status indicator, and the compose healthcheck's.
 * * `/admin/refresh-analytics` is a privileged action in the topbar, behind a key prompt.
 *
 * Five entries but four gaps: the last is the API's only POST, so it never shows up as an
 * uncovered GET. The identity is 54 GET routes = 50 page-owned + these 4.
 */
export const NON_PAGE_ENDPOINTS: readonly string[] = [
  '/search',
  '/meta/filters',
  '/meta/bounds',
  '/health',
  '/admin/refresh-analytics',
] as const

/** Find the entry matching a pathname, for the page header and the document title. */
export function findRoute(pathname: string): RouteEntry | undefined {
  // Exact match first: `/` would otherwise prefix-match every path and always win.
  const exact = ROUTES.find((entry) => entry.path === pathname)
  if (exact) return exact
  return ROUTES.find((entry) => entry.path !== '/' && pathname.startsWith(entry.path))
}
