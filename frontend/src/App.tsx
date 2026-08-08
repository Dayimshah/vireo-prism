import { QueryClientProvider } from '@tanstack/react-query'
import { useMemo, type ComponentType } from 'react'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'

import { createQueryClient } from '@/api/queries'
import { AppLayout } from '@/components/shell/AppLayout'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AudiencePage } from '@/routes/pages/AudiencePage'
import { CohortsPage } from '@/routes/pages/CohortsPage'
import { ContentPage } from '@/routes/pages/ContentPage'
import { EngagementPage } from '@/routes/pages/EngagementPage'
import { ExperimentsPage } from '@/routes/pages/ExperimentsPage'
import { FunnelsPage } from '@/routes/pages/FunnelsPage'
import { MarketingPage } from '@/routes/pages/MarketingPage'
import { MonetizationPage } from '@/routes/pages/MonetizationPage'
import { OverviewPage } from '@/routes/pages/OverviewPage'
import { RetentionPage } from '@/routes/pages/RetentionPage'
import { SessionsPage } from '@/routes/pages/SessionsPage'
import { ROUTES } from '@/routes/registry'
import { FilterProvider } from '@/state/filters'
import { ThemeProvider } from '@/state/theme'

/**
 * Providers and routes.
 *
 * The nesting order is forced, not stylistic
 * -----------------------------------------
 * Each layer depends on the one outside it:
 *
 * 1. `QueryClientProvider` — `FilterProvider` calls `useApi` for `/meta/bounds`, so the
 *    client has to exist first.
 * 2. `BrowserRouter` — `FilterProvider` keeps the window and filters in the query string via
 *    `useSearchParams`, which throws outside a router.
 * 3. `FilterProvider` — every page and the shell read the window from it.
 *
 * `ThemeProvider` and `TooltipProvider` have no such dependency. Theme sits high so a
 * failure inside it cannot leave the app unstyled, and `TooltipProvider` wraps the routes
 * because Radix requires one ancestor provider per tooltip and the shell has several.
 *
 * `Routes` rather than `createBrowserRouter`
 * ----------------------------------------
 * The data router's loaders and actions would be a second place fetching happens, beside
 * react-query — two caches, two loading states, and a decision at every call site about
 * which owns a given request. React-query already owns it, so the routes here are pure
 * layout. The data router also cannot express "wait for the dataset bounds before any page
 * renders" without a loader on every route.
 *
 * Eleven routes, eleven components
 * -------------------------------
 * Phase 10 pointed every route at a single placeholder that read the registry for what the page
 * would eventually be built from. Phase 11 replaces it with {@link PAGES}, so the registry keeps
 * owning order, labels and the endpoint checklist while this map owns only what renders.
 *
 * Statically imported rather than lazy
 * ----------------------------------
 * `React.lazy` per route would trim the initial bundle, but it also puts a suspense boundary
 * between a nav click and the first paint — and every page already opens with skeletons while its
 * panels fetch. Two staged waits for one navigation is worse than a larger first load for an
 * internal dashboard behind a login. If the bundle becomes a problem, splitting Recharts out is
 * the larger win and does not change the routing.
 */
/**
 * What renders at each registry path.
 *
 * Keyed by path rather than listed in order, so this map and `ROUTES` cannot drift into
 * disagreement about which page is which — an array of eleven components matched positionally
 * against an array of eleven routes would swap two pages silently on a reorder.
 *
 * `RouteEntry.path` is typed `string`, not a literal union, so the compiler cannot prove this map
 * is exhaustive. A path with no entry falls back to {@link NotFound} rather than crashing on an
 * undefined element type, and the sidebar link will lead somewhere that says so.
 */
const PAGES: Record<string, ComponentType> = {
  '/': OverviewPage,
  '/engagement': EngagementPage,
  '/retention': RetentionPage,
  '/sessions': SessionsPage,
  '/content': ContentPage,
  '/funnels': FunnelsPage,
  '/cohorts': CohortsPage,
  '/monetization': MonetizationPage,
  '/marketing': MarketingPage,
  '/audience': AudiencePage,
  '/experiments': ExperimentsPage,
}

export function App() {
  // Created once per mount rather than at module scope, so the cache does not outlive the
  // app in a hot reload and a test can mount a clean one. `useMemo` with an empty dependency
  // list is the idiom react-query documents for this.
  const queryClient = useMemo(() => createQueryClient(), [])

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter>
          <FilterProvider>
            <TooltipProvider delayDuration={200}>
              <Routes>
                <Route element={<AppLayout />}>
                  {ROUTES.map((entry) => {
                    const Page = PAGES[entry.path] ?? NotFound
                    return (
                      <Route
                        key={entry.path}
                        // The index route needs `index` rather than `path="/"`, or it competes
                        // with the layout's own path matching.
                        {...(entry.path === '/' ? { index: true } : { path: entry.path })}
                        element={<Page />}
                      />
                    )
                  })}

                  {/* Inside the layout, so a mistyped URL still shows the navigation the
                      reader needs to get out of it. */}
                  <Route path="*" element={<NotFound />} />
                </Route>
              </Routes>
            </TooltipProvider>
          </FilterProvider>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  )
}

/**
 * An unmatched path.
 *
 * Worth distinguishing from an empty dashboard: a bookmark from an earlier version of the
 * app, or a hand-edited URL, otherwise looks like a page whose data failed to load.
 */
function NotFound() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">No such page</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          That address does not match any dashboard. It may be a link from an earlier version
          of the app.
        </p>
        <Button asChild variant="outline" size="sm">
          {/* `asChild` so the button renders as the link itself — a `<button>` wrapping an
              `<a>` is not keyboard-navigable as a link. */}
          <Link to="/">Go to Overview</Link>
        </Button>
      </CardContent>
    </Card>
  )
}
