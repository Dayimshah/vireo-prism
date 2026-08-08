import { Menu } from 'lucide-react'

import { SearchPalette } from './SearchPalette'
import { ServiceStatus } from './ServiceStatus'
import { ThemeToggle } from './ThemeToggle'
import { WindowPicker } from './WindowPicker'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'

/**
 * The controls that apply to every page: search, the reporting window, health, theme.
 *
 * No page title here
 * -----------------
 * The title and its one-line description are rendered by {@link AppLayout} as a page header,
 * directly below this bar. Putting the title in both places would print it twice on every
 * screen — including narrow ones, where the header is the only place it appears.
 *
 * The window picker sits in the topbar rather than the filter bar
 * -------------------------------------------------------------
 * Every endpoint in the API requires `date_from` and `date_to`; the seven dimension filters
 * are all optional. The window is not one filter among several, it is the precondition for
 * any figure existing at all, so it sits with the global controls and the optional narrowing
 * sits in the row below.
 */
export function Topbar({ onOpenSidebar }: { onOpenSidebar: () => void }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-background px-3 sm:px-4">
      {/* Below `lg` the sidebar is an overlay, so it needs a trigger. Hidden above that
          width, where the sidebar is always present and a menu button would do nothing. */}
      <Button
        variant="ghost"
        size="icon-sm"
        className="lg:hidden"
        onClick={onOpenSidebar}
        aria-label="Open navigation"
      >
        <Menu />
      </Button>

      {/* Hidden on the narrowest screens rather than collapsed to an icon: a search field
          that opens over the whole viewport is a second layout to maintain, and search is a
          convenience here — it navigates nowhere except by applying a genre filter. */}
      <div className="hidden flex-1 sm:block">
        <SearchPalette />
      </div>

      {/* Keeps the controls right-aligned when the search field is hidden. */}
      <div className="flex-1 sm:hidden" />

      <div className="flex shrink-0 items-center gap-1.5">
        <WindowPicker />
        <Separator orientation="vertical" className="mx-0.5 h-6" />
        <ServiceStatus />
        <ThemeToggle />
      </div>
    </header>
  )
}
