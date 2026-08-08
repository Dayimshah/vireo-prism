import { NavLink } from 'react-router-dom'

import { APP_NAME, ORG_NAME } from '@/api/config'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { ROUTES } from '@/routes/registry'

/**
 * The primary navigation, rendered from {@link ROUTES}.
 *
 * Every entry comes from the registry, so a page cannot exist in the router and not here —
 * the failure that produces a route no reader can reach. The order is the registry's, which
 * mirrors `app/routers/__init__.py`, so the sidebar and `/docs` introduce the product in the
 * same sequence.
 *
 * `NavLink` rather than a hand-rolled active check
 * -----------------------------------------------
 * It compares against the resolved route, which matters for `/`: a `startsWith` check would
 * mark Overview active on every page. `end` is set on the index route for the same reason.
 *
 * The description is rendered, not tooltipped
 * ------------------------------------------
 * Eleven page names alone do not tell a reader where retention ends and cohorts begin — both
 * plausibly cover "who comes back". One line of prose under each label costs vertical space
 * that this list has to spare, and answers the question at the point it is asked.
 */
export function Sidebar({
  onNavigate,
  className,
}: {
  /** Called after a link is followed, so the mobile overlay can close itself. */
  onNavigate?: () => void
  className?: string
}) {
  return (
    <nav
      aria-label="Dashboards"
      className={cn('flex h-full w-64 shrink-0 flex-col border-r bg-card', className)}
    >
      <div className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        {/* The mark is drawn inline rather than loaded: one element, no request, and it
            inherits the theme's primary colour instead of shipping two PNGs. */}
        <span
          aria-hidden
          className="size-5 shrink-0 rounded-[0.3rem] bg-primary"
          style={{
            // A prism: a triangle notched out of the square, so the mark reads as refraction
            // rather than as a rounded rectangle.
            clipPath: 'polygon(50% 8%, 92% 88%, 8% 88%)',
          }}
        />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold leading-none">{APP_NAME}</p>
          <p className="truncate text-2xs text-muted-foreground">{ORG_NAME} analytics</p>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <ul className="space-y-0.5 p-2">
          {ROUTES.map((entry) => (
            <li key={entry.path}>
              <NavLink
                to={entry.path}
                // Without `end`, the index route's path is a prefix of every other and
                // Overview would render active throughout the app.
                end={entry.path === '/'}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    'group flex items-start gap-2.5 rounded-md px-2 py-2 transition-colors',
                    isActive
                      ? 'bg-accent text-accent-foreground'
                      : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <entry.icon
                      className={cn(
                        'mt-0.5 size-4 shrink-0',
                        isActive ? 'text-primary' : 'text-muted-foreground',
                      )}
                    />
                    <span className="min-w-0">
                      <span
                        className={cn(
                          'block truncate text-xs',
                          isActive ? 'font-semibold' : 'font-medium',
                        )}
                      >
                        {entry.label}
                      </span>
                      <span className="mt-0.5 block text-2xs leading-snug text-muted-foreground">
                        {entry.description}
                      </span>
                    </span>
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </ScrollArea>
    </nav>
  )
}
