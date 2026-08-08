import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { FilterBar } from './FilterBar'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { APP_NAME } from '@/api/config'
import { ProblemError } from '@/api/problem'
import { ErrorState, LoadingState, UnseededState } from '@/components/state/QueryBoundary'
import { Button } from '@/components/ui/button'
import { findRoute } from '@/routes/registry'
import { useFilters } from '@/state/filters'

/**
 * The frame every page renders inside.
 *
 * The metadata gate lives here, once, rather than in eleven pages
 * -------------------------------------------------------------
 * Every analytics endpoint requires a reporting window, and a window cannot exist until
 * `/meta/bounds` has answered. Three outcomes are possible before any page can show a figure,
 * and they need different words:
 *
 * * **loading** — the bounds are in flight. Skeletons.
 * * **unreachable** — the request failed. The API is down, or `VITE_API_BASE_URL` is wrong,
 *   or CORS is refusing the origin. A page rendering its own eleven failures would bury
 *   that one cause under eleven symptoms.
 * * **unseeded** — the database is migrated and empty, so there are no activity dates to
 *   build a window from. The fix is `make seed`, and it is the only one of the three a
 *   reader can carry out themselves.
 *
 * Gating here means each is said once, in the place where the page content would have gone,
 * with the shell's controls still visible so the reader can see what they were asking for.
 *
 * The filter bar is global because the filters are
 * ----------------------------------------------
 * All 49 analytics endpoints accept the same seven dimensions plus `is_premium` — checked
 * against the generated schema, not assumed. So the bar belongs to the frame. The reporting
 * window is *not* uniform (five endpoints reject it, see `windowParamsFor`), but that
 * asymmetry is handled per-request rather than by hiding a control.
 */
export function AppLayout() {
  const location = useLocation()
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  const { isLoadingMeta, metaError, isUnseeded } = useFilters()

  const route = findRoute(location.pathname)

  // The tab title names the page, so a reader with several dashboards open can tell them
  // apart. Set in an effect rather than by a helmet library: this is two lines and one
  // dependency avoided.
  useEffect(() => {
    document.title = route ? `${route.label} · ${APP_NAME}` : APP_NAME
  }, [route])

  // A route change closes the mobile overlay. Without this, following a link leaves the
  // sidebar covering the page that was just navigated to.
  useEffect(() => {
    setIsSidebarOpen(false)
  }, [location.pathname])

  return (
    <div className="flex h-dvh overflow-hidden bg-background text-foreground">
      {/* Persistent from `lg` up. Hidden below it, where the overlay below takes over. */}
      <Sidebar className="hidden lg:flex" />

      {isSidebarOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          {/* The scrim is a button so Escape and a click both close the sheet, and so screen
              readers announce it as dismissable rather than as decoration. */}
          <button
            type="button"
            aria-label="Close navigation"
            className="absolute inset-0 bg-background/80 backdrop-blur-sm"
            onClick={() => setIsSidebarOpen(false)}
          />
          <div className="relative flex">
            <Sidebar onNavigate={() => setIsSidebarOpen(false)} />
            <Button
              variant="ghost"
              size="icon-sm"
              className="absolute right-2 top-2"
              onClick={() => setIsSidebarOpen(false)}
              aria-label="Close navigation"
            >
              <X />
            </Button>
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onOpenSidebar={() => setIsSidebarOpen(true)} />

        {/* `min-w-0` on the column above and `overflow-auto` here: without the first, a wide
            table inside a flex child pushes the whole layout sideways instead of scrolling
            within its own container. */}
        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-[100rem] space-y-4 p-3 sm:p-4">
            {route && (
              <div>
                <h1 className="text-lg font-semibold leading-tight">{route.label}</h1>
                <p className="mt-0.5 text-xs text-muted-foreground">{route.description}</p>
              </div>
            )}

            <FilterBar />

            {/* Order matters: unseeded before error. An unseeded database also makes the
                analytics queries fail, and reporting a downstream failure would send a
                reader to debug a query when the fix is one command. */}
            {isUnseeded ? (
              <UnseededState />
            ) : metaError ? (
              <ErrorState
                error={
                  metaError instanceof ProblemError
                    ? metaError
                    : // A non-`ProblemError` here means the request never reached the API —
                      // `fromNetworkFailure` names all three indistinguishable causes rather
                      // than guessing one.
                      ProblemError.fromNetworkFailure(metaError, '/meta/bounds')
                }
              />
            ) : isLoadingMeta ? (
              <LoadingState rows={4} />
            ) : (
              <Outlet />
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
