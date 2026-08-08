import { Download, Info } from 'lucide-react'
import type { ReactNode } from 'react'

import type { ResponseInfo } from '@/api/client'
import { CacheBadge } from '@/components/state/CacheBadge'
import { QueryBoundary, type QueryBoundaryProps } from '@/components/state/QueryBoundary'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

/**
 * The frame every chart and table sits in: title, definition, cache badge, export, states.
 *
 * Exists so a page reads as a list of questions rather than a list of layout code, and so
 * the four states from {@link QueryBoundary} are never hand-rolled per chart — the failure
 * mode there is a chart that renders an empty axis for an empty result, which reads as
 * "all zeros" rather than "no rows".
 *
 * The `definition` slot is not decoration. Several figures in this API are easy to
 * misread — `avg_dau` is a mean of daily counts and cannot be summed, unbounded retention
 * is not monotonic across `day_n` because the cohort shrinks with the eligibility rule — and
 * the place a reader looks for that is beside the number.
 */
export interface ChartCardProps extends Omit<QueryBoundaryProps, 'children' | 'className'> {
  title: string

  /** One sentence on what the figure means. Shown behind an info icon beside the title. */
  definition?: string

  /** Transport facts for the cache badge. */
  info?: ResponseInfo | undefined

  /** Called when the export button is pressed. Omit to hide the button. */
  onExport?: () => void

  /** Extra controls for the header — a segment selector, a metric toggle. */
  actions?: ReactNode

  /** Fixed height for the body, so a loading card does not resize when data lands. */
  bodyClassName?: string

  className?: string
  children: ReactNode
}

export function ChartCard({
  title,
  definition,
  info,
  onExport,
  actions,
  bodyClassName,
  className,
  children,
  ...boundary
}: ChartCardProps) {
  // Exporting an empty result would save a header-only file, which looks like data loss.
  const canExport = onExport && !boundary.isPending && !boundary.error && !boundary.isEmpty

  return (
    <Card className={cn('flex flex-col', className)}>
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div className="flex min-w-0 items-center gap-1.5">
          <CardTitle className="truncate">{title}</CardTitle>
          {definition && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  // A real button, not a bare icon: the definition has to be reachable by
                  // keyboard, and Radix only wires focus handling to a focusable child.
                  className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`What ${title} measures`}
                >
                  <Info className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent>{definition}</TooltipContent>
            </Tooltip>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {actions}
          <CacheBadge info={info} />
          {onExport && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={onExport}
                  disabled={!canExport}
                  aria-label={`Export ${title} as CSV`}
                >
                  <Download />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Export as CSV</TooltipContent>
            </Tooltip>
          )}
        </div>
      </CardHeader>

      <CardContent className={cn('flex-1', bodyClassName)}>
        <QueryBoundary {...boundary}>{children}</QueryBoundary>
      </CardContent>
    </Card>
  )
}
