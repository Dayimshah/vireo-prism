import { Film, Hash, Search, Tag, User } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { LucideIcon } from 'lucide-react'

import { useApi } from '@/api/queries'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverAnchor } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { humanize } from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * Global lookup across the catalogue.
 *
 * What search actually returns, which is not what the API says it returns
 * ---------------------------------------------------------------------
 * `app/routers/search.py` documents "content, users and experiments". The SQL behind it —
 * `search/global_search_union.sql` — unions four different things: `content`, `user`,
 * `session` and `genre`. **There are no experiment results.** The docstring is wrong, the SQL
 * is the contract, and this component follows the SQL.
 *
 * That is a backend documentation defect, not a frontend one, so it is recorded here rather
 * than worked around: `RESULT_KINDS` covers the four kinds the query can actually emit, and
 * an unrecognised `result_type` still renders with a generic icon — if experiments are added
 * later, they appear as a plain row instead of vanishing.
 *
 * Only genre results lead anywhere
 * -------------------------------
 * `result_id` is an integer for all four kinds, and none of the eleven pages take a
 * `content_id`, `user_id` or `session_id` — there are no detail pages in this product. So
 * three of the four kinds are **informational**: search confirms a title exists and tells you
 * its genre and year, and that is the whole of it.
 *
 * `genre` is the exception, because genre *is* one of the seven query parameters. A genre hit
 * is therefore offered as a filter, and clicking it narrows every page. Presenting the other
 * three as though they were clickable would promise navigation this app does not have.
 *
 * Debounced, not fired per keystroke
 * ---------------------------------
 * The rate limiter allows 60 requests per client per window. Typing "shadow" unthrottled is
 * six requests, five of which are already stale by the time they land — and the two-character
 * minimum means the first one is a guaranteed 422. A 250ms pause is below the point where
 * typing feels laggy and cuts a word to one request.
 */

/** How each `result_type` the SQL emits is presented. */
const RESULT_KINDS: Record<string, { icon: LucideIcon; label: string; actionable: boolean }> = {
  content: { icon: Film, label: 'Title', actionable: false },
  genre: { icon: Tag, label: 'Genre', actionable: true },
  user: { icon: User, label: 'User', actionable: false },
  session: { icon: Hash, label: 'Session', actionable: false },
}

/** The API's own minimum — `q` is documented as at least two characters. */
const MIN_QUERY_LENGTH = 2

const DEBOUNCE_MS = 250

export function SearchPalette() {
  const [draft, setDraft] = useState('')
  const [term, setTerm] = useState('')
  const [isOpen, setIsOpen] = useState(false)

  const { filters, setFilter } = useFilters()

  // Debounce inline rather than through a shared hook: this is the only consumer, and the
  // cleanup is what does the work — every keystroke cancels the previous timer, so only a
  // pause in typing ever reaches `term`.
  useEffect(() => {
    const handle = setTimeout(() => setTerm(draft.trim()), DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [draft])

  const isLongEnough = term.length >= MIN_QUERY_LENGTH

  const search = useApi(
    '/search',
    { q: term, limit: 20 },
    {
      // Below two characters the API answers 422, so the query is not issued at all rather
      // than issued and the error swallowed.
      enabled: isLongEnough,
      // Results are a function of the term alone — no window, no filters — so they stay
      // usable far longer than an analytics response.
      staleTime: 5 * 60 * 1000,
    },
  )

  const rows = useMemo(() => search.payload?.data ?? [], [search.payload])

  // Opened by typing rather than by clicking the field: an empty popover on focus is a
  // flash of nothing on the way to the first keystroke.
  useEffect(() => {
    if (draft.trim().length >= MIN_QUERY_LENGTH) setIsOpen(true)
  }, [draft])

  const applyGenre = (name: string) => {
    if (!filters.genre.includes(name)) setFilter('genre', [...filters.genre, name])
    setIsOpen(false)
    setDraft('')
  }

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverAnchor asChild>
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setIsOpen(false)
                // The term is kept so reopening does not refetch what is already cached.
              }
            }}
            placeholder="Search titles, genres, ids…"
            aria-label="Search the catalogue"
            className="h-8 pl-8 text-xs"
          />
        </div>
      </PopoverAnchor>

      <PopoverContent
        className="w-80 p-0"
        align="start"
        // Focus stays in the input so typing continues to narrow the list. Without this
        // Radix moves focus into the popover and the next keystroke is lost.
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        {!isLongEnough ? (
          <p className="px-3 py-4 text-2xs text-muted-foreground">
            Type at least {MIN_QUERY_LENGTH} characters. Numbers are matched against user,
            session and title ids as well as text.
          </p>
        ) : search.isPending ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : search.isError ? (
          // `message` rather than `problem.title`: the constructor already prefers the
          // API's `detail`, which is the sentence written for a reader, and falls back to
          // the title when there is none.
          <p className="px-3 py-4 text-2xs text-negative">{search.error.message}</p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-4 text-2xs text-muted-foreground">
            Nothing matches “{term}”. Users and sessions are only found by id — they have no
            searchable name.
          </p>
        ) : (
          <ScrollArea className="max-h-80">
            <div className="p-1">
              {rows.map((row) => {
                const kind = RESULT_KINDS[row.result_type]
                const Icon = kind?.icon ?? Hash
                const isActionable = kind?.actionable ?? false

                const content = (
                  <>
                    <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">{row.label}</span>
                      <span className="block truncate text-2xs text-muted-foreground">
                        {row.sublabel}
                      </span>
                    </span>
                    <Badge variant="muted" className="mt-0.5 shrink-0">
                      {kind?.label ?? humanize(row.result_type)}
                    </Badge>
                  </>
                )

                // Keyed on both columns: `result_id` is documented as unique within a
                // `result_type` and not across them, so a content id and a genre id can
                // collide.
                const key = `${row.result_type}:${row.result_id}`

                return isActionable ? (
                  <button
                    key={key}
                    type="button"
                    onClick={() => applyGenre(row.label)}
                    className="flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:outline-none"
                  >
                    {content}
                  </button>
                ) : (
                  // A plain div, deliberately. These kinds have nowhere to go, and a
                  // hover state on an inert row is a promise of a click that does nothing.
                  <div key={key} className="flex items-start gap-2 px-2 py-1.5">
                    {content}
                  </div>
                )
              })}
            </div>
          </ScrollArea>
        )}

        <div className="border-t px-3 py-2">
          <p className="text-2xs text-muted-foreground">
            Genres apply as a filter. Titles, users and sessions are shown for reference —
            this product has no per-record pages.
          </p>
        </div>
      </PopoverContent>
    </Popover>
  )
}
