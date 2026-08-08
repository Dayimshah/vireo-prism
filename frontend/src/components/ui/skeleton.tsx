import { cn } from '@/lib/utils'

/**
 * A loading placeholder.
 *
 * A sweep rather than a pulse (see `tailwind.config.ts`): a pulsing rectangle where a
 * chart will be reads as a chart with animated data, which is worse than obviously empty.
 *
 * `aria-hidden` with a `role="status"` wrapper is the accessible pattern — a screen reader
 * should hear "loading" once, not a description of every grey box. `QueryBoundary` supplies
 * that wrapper.
 */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn('relative overflow-hidden rounded-md bg-muted', className)}
      {...props}
    >
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-background/40 to-transparent" />
    </div>
  )
}

export { Skeleton }
