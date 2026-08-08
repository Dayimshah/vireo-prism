import { Check, ChevronDown, ListFilter, X } from 'lucide-react'
import { useMemo, useState } from 'react'

import type { FilterOptions } from '@/api/endpoints'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { humanize, pluralize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { FILTER_KEYS, useFilters, type FilterKey } from '@/state/filters'

/**
 * The filter row: one control per queryable dimension, plus the applied-value chips.
 *
 * Seven params, eleven catalogue dimensions, and they do not line up
 * ----------------------------------------------------------------
 * `/meta/filters` returns eleven dimensions; only eight values are actually accepted as
 * query parameters, and the two sets overlap without either containing the other:
 *
 * * `platform`, `form_factor`, `channel_group`, `plan`, `plan_tier` are **catalogue-only**.
 *   They exist to populate `segment_by` pickers on the retention and funnel pages. Offering
 *   them here would build a control whose value no endpoint accepts.
 * * `language` is the reverse: **a parameter with no catalogue**. There is no allowlist to
 *   render as a checkbox list, so it takes free text.
 *
 * The split is derived from the generated schema rather than written out — see
 * {@link CatalogueKey}. If the API adds a catalogue for `language`, or drops one, this file
 * stops compiling instead of quietly rendering an empty list.
 *
 * Why applied values are chips rather than a count
 * -----------------------------------------------
 * `language` has no allowlist, and the API documents that an unrecognised value narrows the
 * result to nothing rather than raising. So `?language=Enlish` returns a clean `200` with
 * empty data on every chart, and a collapsed "1 filter" badge gives the reader nothing to
 * notice. Rendering each applied value as a removable chip makes the typo visible at the
 * point where the charts went blank.
 */

/**
 * The filter keys that have an allowlist behind them.
 *
 * `Extract` against `keyof FilterOptions` rather than a hand-written list: the six that are
 * both a query parameter and a catalogue dimension, computed. A transcribed list would be
 * correct today and silently wrong after a schema change.
 */
type CatalogueKey = Extract<FilterKey, keyof FilterOptions>

/** Runtime counterpart of {@link CatalogueKey}. The annotation is what enforces the match. */
const CATALOGUE_KEYS: readonly CatalogueKey[] = [
  'country',
  'channel',
  'persona',
  'device',
  'genre',
  'content_type',
] as const

/**
 * The keys that take free text because no catalogue exists for them.
 *
 * Derived by subtraction rather than listed, so a dimension can only ever be in one of the
 * two groups — a key added to `FILTER_KEYS` and forgotten here still gets a control.
 */
const FREE_TEXT_KEYS: readonly FilterKey[] = FILTER_KEYS.filter(
  (key) => !(CATALOGUE_KEYS as readonly string[]).includes(key),
)

/** Above this many options the popover grows a search box. */
const SEARCH_THRESHOLD = 8

/** One dimension's checkbox list, behind a popover. */
function CatalogueFilter({
  dimension,
  available,
  selected,
  onChange,
}: {
  dimension: CatalogueKey
  available: readonly string[]
  selected: readonly string[]
  onChange: (values: string[]) => void
}) {
  const [search, setSearch] = useState('')
  const label = humanize(dimension)

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return available
    return available.filter((value) => value.toLowerCase().includes(needle))
  }, [available, search])

  // The catalogue is empty until `/meta/filters` responds, and stays empty for an unseeded
  // database. Disabled rather than hidden: a control that appears once data loads makes the
  // row jump, and a reader who saw six filters yesterday should still see six.
  const isEmpty = available.length === 0

  const toggle = (value: string) => {
    onChange(
      selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value],
    )
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={isEmpty}
          className={cn('gap-1.5', selected.length > 0 && 'border-primary/50')}
        >
          <span className="text-xs">{label}</span>
          {selected.length > 0 && (
            <Badge variant="secondary" className="px-1 py-0 tabular">
              {selected.length}
            </Badge>
          )}
          <ChevronDown className="opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-56 p-0" align="start">
        {available.length > SEARCH_THRESHOLD && (
          <div className="border-b p-2">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={`Search ${label.toLowerCase()}…`}
              className="h-8 text-xs"
              // Not `type="search"`: the native clear affordance sits inside the field and
              // is styled inconsistently across browsers, and the value clears with the
              // popover anyway.
              aria-label={`Search ${label}`}
            />
          </div>
        )}

        <ScrollArea className="max-h-64">
          <div className="p-1">
            {visible.length === 0 ? (
              <p className="px-2 py-3 text-2xs text-muted-foreground">
                Nothing matches “{search.trim()}”.
              </p>
            ) : (
              visible.map((value) => {
                const isSelected = selected.includes(value)
                return (
                  <button
                    key={value}
                    type="button"
                    // A button rather than a Radix checkbox item: the popover must stay open
                    // across several selections, and a menu item closes on choose.
                    role="checkbox"
                    aria-checked={isSelected}
                    onClick={() => toggle(value)}
                    className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:outline-none"
                  >
                    <span
                      className={cn(
                        'flex size-4 shrink-0 items-center justify-center rounded-sm border',
                        isSelected ? 'border-primary bg-primary text-primary-foreground' : 'border-input',
                      )}
                    >
                      {isSelected && <Check className="size-3" strokeWidth={3} />}
                    </span>
                    <span className="truncate">{value}</span>
                  </button>
                )
              })
            )}
          </div>
        </ScrollArea>

        {selected.length > 0 && (
          <div className="border-t p-1">
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start text-xs"
              onClick={() => onChange([])}
            >
              Clear {label.toLowerCase()}
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

/**
 * A free-text dimension: `language`.
 *
 * Submitted on Enter rather than on every keystroke. Each keystroke would push a history
 * entry and fire a round of queries for `E`, `En`, `Eng` — three narrowings to nothing on
 * the way to one real value.
 */
function FreeTextFilter({
  dimension,
  selected,
  onChange,
}: {
  dimension: FilterKey
  selected: readonly string[]
  onChange: (values: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const label = humanize(dimension)

  const commit = () => {
    const value = draft.trim()
    if (!value) return
    if (!selected.includes(value)) onChange([...selected, value])
    setDraft('')
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn('gap-1.5', selected.length > 0 && 'border-primary/50')}
        >
          <span className="text-xs">{label}</span>
          {selected.length > 0 && (
            <Badge variant="secondary" className="px-1 py-0 tabular">
              {selected.length}
            </Badge>
          )}
          <ChevronDown className="opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-64" align="start">
        <div className="space-y-2">
          <div>
            <p className="text-xs font-medium">{label}</p>
            {/* Stated plainly, because this is the one filter that can fail silently. */}
            <p className="mt-0.5 text-2xs text-muted-foreground">
              No list of accepted values exists for this dimension. A value the catalogue does
              not contain returns no rows rather than an error, so check the spelling if every
              chart empties.
            </p>
          </div>

          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commit()
              }
            }}
            placeholder="e.g. English, then Enter"
            className="h-8 text-xs"
            aria-label={`Add ${label}`}
          />

          {selected.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {selected.map((value) => (
                <Badge key={value} variant="muted" className="gap-1">
                  {value}
                  <button
                    type="button"
                    onClick={() => onChange(selected.filter((item) => item !== value))}
                    aria-label={`Remove ${label} ${value}`}
                    className="rounded-full transition-colors hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}

/**
 * The tri-state premium filter.
 *
 * Three explicit buttons, not a switch or a checkbox. `null` means "both" and is a genuinely
 * different query from `false`, which means "unpaid only" — a two-state control has nowhere
 * to put the third value, and collapsing `null` into `false` would silently narrow every
 * chart from all users to unpaid users while still answering `200`.
 *
 * The label says "currently" because `is_premium` reflects subscription state now, not
 * during the reporting window. A user who cancelled last week is `false` for a window in
 * which they were paying.
 */
function PremiumFilter() {
  const { filters, setIsPremium } = useFilters()

  const OPTIONS: readonly { value: boolean | null; label: string }[] = [
    { value: null, label: 'All' },
    { value: true, label: 'Premium' },
    { value: false, label: 'Free' },
  ]

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className="inline-flex items-center rounded-md border p-0.5"
          role="radiogroup"
          aria-label="Subscription state"
        >
          {OPTIONS.map((option) => {
            const isActive = filters.is_premium === option.value
            return (
              <button
                key={String(option.value)}
                type="button"
                role="radio"
                aria-checked={isActive}
                onClick={() => setIsPremium(option.value)}
                className={cn(
                  'rounded-sm px-2 py-1 text-xs transition-colors',
                  isActive
                    ? 'bg-secondary font-medium text-secondary-foreground'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {option.label}
              </button>
            )
          })}
        </div>
      </TooltipTrigger>
      <TooltipContent>
        Subscription state as it stands now, not during the window. Someone who cancelled last
        week counts as Free for a window in which they were paying.
      </TooltipContent>
    </Tooltip>
  )
}

/** The chips for everything currently applied, each removable. */
function AppliedChips() {
  const { filters, setFilter, setIsPremium, activeFilterCount, clearFilters } = useFilters()

  if (activeFilterCount === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-1">
      {FILTER_KEYS.flatMap((key) =>
        filters[key].map((value) => (
          <Badge key={`${key}:${value}`} variant="muted" className="gap-1">
            <span className="text-muted-foreground">{humanize(key)}</span>
            <span className="font-medium text-foreground">{value}</span>
            <button
              type="button"
              onClick={() => setFilter(key, filters[key].filter((item) => item !== value))}
              aria-label={`Remove ${humanize(key)} ${value}`}
              className="rounded-full transition-colors hover:text-foreground"
            >
              <X className="size-3" />
            </button>
          </Badge>
        )),
      )}

      {filters.is_premium !== null && (
        <Badge variant="muted" className="gap-1">
          <span className="text-muted-foreground">Subscription</span>
          <span className="font-medium text-foreground">
            {filters.is_premium ? 'Premium' : 'Free'}
          </span>
          <button
            type="button"
            onClick={() => setIsPremium(null)}
            aria-label="Remove subscription filter"
            className="rounded-full transition-colors hover:text-foreground"
          >
            <X className="size-3" />
          </button>
        </Badge>
      )}

      <Button variant="ghost" size="sm" className="h-6 px-2 text-2xs" onClick={clearFilters}>
        Clear all
      </Button>
    </div>
  )
}

export function FilterBar({ className }: { className?: string }) {
  const { filters, options, setFilter, activeFilterCount } = useFilters()

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 inline-flex items-center gap-1.5 text-2xs font-medium text-muted-foreground">
          <ListFilter className="size-3.5" />
          Filters
        </span>

        {CATALOGUE_KEYS.map((key) => (
          <CatalogueFilter
            key={key}
            dimension={key}
            // Every field on `FilterOptions` is optional, so an absent catalogue is a real
            // possibility rather than defensive coding — the endpoint omits a dimension it
            // has no values for.
            available={options?.[key] ?? []}
            selected={filters[key]}
            onChange={(values) => setFilter(key, values)}
          />
        ))}

        {FREE_TEXT_KEYS.map((key) => (
          <FreeTextFilter
            key={key}
            dimension={key}
            selected={filters[key]}
            onChange={(values) => setFilter(key, values)}
          />
        ))}

        <Separator orientation="vertical" className="mx-1 h-6" />

        <PremiumFilter />

        {activeFilterCount > 0 && (
          <span className="ml-auto text-2xs text-muted-foreground">
            {pluralize(activeFilterCount, 'filter')} applied
          </span>
        )}
      </div>

      <AppliedChips />
    </div>
  )
}
