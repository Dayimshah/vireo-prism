import { CalendarRange } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  formatWindow,
  matchPreset,
  resolvePreset,
  windowLength,
  WINDOW_PRESETS,
} from '@/lib/dates'
import { pluralize } from '@/lib/format'
import { useFilters } from '@/state/filters'
import { cn } from '@/lib/utils'

/**
 * The reporting-window control.
 *
 * Native `<input type="date">`, not a JS calendar
 * ----------------------------------------------
 * It supports `min` and `max` natively, which is the whole point here: the browser refuses
 * to offer a date outside the dataset, so the commonest way to produce an empty dashboard
 * is unreachable rather than merely discouraged. It is also keyboard-accessible and
 * localised for free, and it drops a dependency (`react-day-picker`) that would exist only
 * to look more like a design system.
 *
 * Presets are anchored to the data, never to today
 * -----------------------------------------------
 * "Last 30 days" means the 30 days ending on the dataset's `last_activity_date`, not on the
 * current date. The dataset ends 2026-08-06 and today is later, so a `Date.now()`-anchored
 * preset would open every chart empty — which is exactly why the API refuses to default the
 * window at all.
 */
export function WindowPicker() {
  const { window: current, bounds, setWindow, isLoadingMeta } = useFilters()

  // Both date fields are nullable in the schema — an unseeded database has no activity, so
  // it reports both as null. Narrowed to a pair here, once, rather than coalescing each one
  // separately at every use: a half-present pair would otherwise produce a picker with one
  // live bound and one absent, which is a state the dataset cannot actually be in.
  const first = bounds?.first_activity_date ?? undefined
  const last = bounds?.last_activity_date ?? undefined
  const dateBounds =
    first && last ? { first_activity_date: first, last_activity_date: last } : null

  // No window yet: the bounds are still loading, or the database is unseeded. Both are
  // handled by the shell, so this control simply has nothing to offer.
  if (!current || !dateBounds) {
    return (
      <Button variant="outline" size="sm" disabled className="gap-2">
        <CalendarRange />
        <span className="text-xs">{isLoadingMeta ? 'Loading dates…' : 'No date range'}</span>
      </Button>
    )
  }

  const activePreset = matchPreset(current, dateBounds)
  const days = windowLength(current)

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <CalendarRange />
          <span className="hidden text-xs sm:inline">{formatWindow(current)}</span>
          <span className="text-xs sm:hidden">{days === null ? '—' : `${days}d`}</span>
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-80">
        <div className="space-y-3">
          <div>
            <p className="text-xs font-medium">Reporting window</p>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              {/* The range is stated because the API has no default window and a reader
                  cannot otherwise know where the data actually is. */}
              Data runs {first ?? '—'} to {last ?? '—'}. Both ends of your window are
              inclusive.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-2">
            {WINDOW_PRESETS.map((preset) => (
              <Button
                key={preset.id}
                variant={activePreset === preset.id ? 'default' : 'outline'}
                size="sm"
                className="text-xs"
                onClick={() => {
                  if (first && last) {
                    setWindow(resolvePreset(preset, { first_activity_date: first, last_activity_date: last }))
                  }
                }}
              >
                {preset.label}
              </Button>
            ))}
          </div>

          <Separator />

          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label htmlFor="window-from">From</Label>
              <Input
                id="window-from"
                type="date"
                value={current.date_from}
                // Bounded by the dataset, so the picker cannot reach a date with no data.
                // `max` is the other end of the window rather than the dataset's end,
                // which makes a reversed range — the API's 422 — unreachable from the UI.
                min={first}
                max={current.date_to}
                className="h-8 text-xs"
                onChange={(event) => {
                  const value = event.target.value
                  // An empty value happens while the field is being edited. Writing it
                  // would clear the window mid-keystroke and blank every chart.
                  if (value) setWindow({ ...current, date_from: value })
                }}
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="window-to">To</Label>
              <Input
                id="window-to"
                type="date"
                value={current.date_to}
                min={current.date_from}
                max={last}
                className="h-8 text-xs"
                onChange={(event) => {
                  const value = event.target.value
                  if (value) setWindow({ ...current, date_to: value })
                }}
              />
            </div>
          </div>

          <Tooltip>
            <TooltipTrigger asChild>
              <p className={cn('cursor-help text-2xs text-muted-foreground')}>
                {days === null ? '—' : pluralize(days, 'day')} selected
              </p>
            </TooltipTrigger>
            <TooltipContent>
              Counting both endpoints, which is how the API reports{' '}
              <code className="font-mono">meta.window.days</code>.
            </TooltipContent>
          </Tooltip>
        </div>
      </PopoverContent>
    </Popover>
  )
}
