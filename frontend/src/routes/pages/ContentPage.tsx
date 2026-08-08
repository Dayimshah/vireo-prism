import { useMemo } from 'react'

import { usePanel } from '@/api/panel'
import { CategoryBarChart } from '@/components/charts/CategoryBarChart'
import { ChartCard } from '@/components/charts/ChartCard'
import { DataTable, numericColumn, type Column } from '@/components/charts/DataTable'
import { HeatmapMatrix } from '@/components/charts/HeatmapMatrix'
import { TimeSeriesChart } from '@/components/charts/TimeSeriesChart'
import { Badge } from '@/components/ui/badge'
import { exportRows, type CsvValue } from '@/lib/csv'
import { EMPTY, formatNumber, formatPercent, isAbsent } from '@/lib/format'
import { useFilters } from '@/state/filters'

/**
 * The catalogue: what gets watched, what gets finished, and what each persona reaches for.
 *
 * Six endpoints. Three of them carry a figure that will be read wrongly if it is presented in
 * the obvious way, and each is handled here with the reason stated.
 *
 * `view_to_start_pct` is not a conversion rate
 * ------------------------------------------
 * It reads 238.5% for Anime. That is not a defect: it is starts divided by detail-page views,
 * and a viewer resumes a series from the home rail without passing through its detail page
 * again. So it exceeds 100% wherever repeat viewing is normal, and calling it "conversion"
 * invites a reader to hunt for a bug that is not there. It is labelled *starts per detail view*
 * and rendered as a ratio, not a percentage bar.
 *
 * Affinity is shown as share, and the lift is a column
 * -------------------------------------------------
 * `affinity_lift` is the interesting number — 4.11 means Anime Fans watch Anime at four times
 * the population rate — but it is a ratio centred on 1.0, and the heatmap ramp is linear from
 * zero. On that ramp a lift of exactly 1.0, meaning *no preference at all*, renders as a pale
 * cell indistinguishable from 0.02, meaning *never touches it*. So the matrix is coloured by
 * `pct_of_persona_watch`, which is a true 0-anchored share the ramp describes honestly, and the
 * lift travels in the cell tooltip and the table beneath.
 *
 * A missing affinity pair is zero, not unknown
 * ------------------------------------------
 * The response holds 111 of the 112 possible persona × genre pairs — no Sports Fan watched any
 * Romance in this window. {@link HeatmapMatrix} paints an absent cell with `--heat-null` and
 * explains it as *not yet observable*, which is right for a triangular cohort matrix and wrong
 * here: this grid has no time axis, and no row means no watch time, which for a share of watch
 * is genuinely 0%. So the grid is completed with explicit zeros before it is handed over. The
 * lift and rank for such a cell stay absent, because those are undefined rather than zero.
 */

/** One cell of the completed persona × genre grid. */
interface AffinityCell extends Record<string, CsvValue> {
  genre: string
  persona: string
  /** Share of this persona's watch time. 0 for a pair with no watch time — see the docstring. */
  pct_of_persona_watch: number
  /** Undefined for a filled cell: a ratio against no watch time has no value. */
  affinity_lift: number | null
  rank_within_persona: number | null
  watch_hours: number
}

/** How many titles the ranked bar chart shows. The table below carries all of them. */
const CHART_TITLE_COUNT = 15

/**
 * Separator for the composite `genre`+`persona` lookup key.
 *
 * A NUL cannot occur inside either value, so `"Sci-Fi" + "Binge Watcher"` can never collide with
 * some other pair that happens to concatenate to the same string — which a hyphen or a space
 * could. Constructed rather than written as a literal control character: embedding the raw byte
 * makes the source file binary to grep and to anything that sniffs encoding.
 */
const GRID_KEY_SEP = String.fromCharCode(0)

export function ContentPage() {
  const { window } = useFilters()

  const topWatch = usePanel('/content/top-watch-time')
  const completion = usePanel('/content/completion-rate')
  const genrePerf = usePanel('/content/genre-performance')
  const affinity = usePanel('/content/genre-affinity')
  const shelfLife = usePanel('/content/shelf-life-decay')
  const trailer = usePanel('/content/trailer-to-start')

  // The grid is completed rather than left sparse. See the module docstring.
  const affinityGrid = useMemo<AffinityCell[]>(() => {
    const rows = affinity.rows
    if (rows.length === 0) return []

    const personas = [...new Set(rows.map((row) => row.persona))].sort()
    // Genres in descending order of total watch time, so the heavy end of the catalogue is at
    // the top of the matrix rather than wherever the alphabet puts it.
    const watchByGenre = new Map<string, number>()
    for (const row of rows) {
      watchByGenre.set(row.genre, (watchByGenre.get(row.genre) ?? 0) + row.watch_hours)
    }
    const genres = [...watchByGenre.keys()].sort(
      (a, b) => (watchByGenre.get(b) ?? 0) - (watchByGenre.get(a) ?? 0),
    )

    const found = new Map(rows.map((row) => [`${row.genre}${GRID_KEY_SEP}${row.persona}`, row]))

    return genres.flatMap((genre) =>
      personas.map((persona): AffinityCell => {
        const row = found.get(`${genre}${GRID_KEY_SEP}${persona}`)
        return {
          genre,
          persona,
          pct_of_persona_watch: row?.pct_of_persona_watch ?? 0,
          affinity_lift: row?.affinity_lift ?? null,
          rank_within_persona: row?.rank_within_persona ?? null,
          watch_hours: row?.watch_hours ?? 0,
        }
      }),
    )
  }, [affinity.rows])

  const topWatchChartRows = useMemo(
    () => topWatch.rows.slice(0, CHART_TITLE_COUNT),
    [topWatch.rows],
  )

  const topWatchColumns: Column<(typeof topWatch.rows)[number]>[] = [
    { key: 'watch_rank', header: '#', value: (row) => row.watch_rank, align: 'right' },
    {
      key: 'title',
      header: 'Title',
      value: (row) => row.title,
      className: 'font-medium',
      render: (row) => (
        <span className="flex items-center gap-1.5">
          {row.title}
          {row.is_original && (
            <Badge variant="secondary" className="shrink-0">
              original
            </Badge>
          )}
        </span>
      ),
    },
    { key: 'genre', header: 'Genre', value: (row) => row.genre },
    { key: 'content_type', header: 'Type', value: (row) => row.content_type },
    { key: 'release_year', header: 'Year', value: (row) => row.release_year, align: 'right' },
    { key: 'language', header: 'Language', value: (row) => row.language },
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    numericColumn('unique_viewers', 'Viewers', (row) => row.unique_viewers, 'users'),
    numericColumn(
      'watch_hours_per_viewer',
      'Hours per viewer',
      (row) => row.watch_hours_per_viewer,
    ),
    numericColumn('starts', 'Starts', (row) => row.starts),
    {
      ...numericColumn(
        'completion_rate_pct',
        'Completion',
        (row) => row.completion_rate_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.completion_rate_pct),
    },
    numericColumn('detail_views', 'Detail views', (row) => row.detail_views),
    numericColumn('watchlist_adds', 'Watchlist adds', (row) => row.watchlist_adds),
  ]

  const completionColumns: Column<(typeof completion.rows)[number]>[] = [
    {
      key: 'title',
      header: 'Title',
      value: (row) => row.title,
      className: 'font-medium',
      render: (row) => (
        <span className="flex items-center gap-1.5">
          {row.title}
          {row.is_original && (
            <Badge variant="secondary" className="shrink-0">
              original
            </Badge>
          )}
        </span>
      ),
    },
    { key: 'genre', header: 'Genre', value: (row) => row.genre },
    { key: 'content_type', header: 'Type', value: (row) => row.content_type },
    numericColumn('runtime_minutes', 'Runtime', (row) => row.runtime_minutes),
    numericColumn('starts', 'Starts', (row) => row.starts),
    numericColumn('completions', 'Completions', (row) => row.completions),
    numericColumn('abandons', 'Abandons', (row) => row.abandons),
    {
      ...numericColumn(
        'completion_rate_pct',
        'Completion',
        (row) => row.completion_rate_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.completion_rate_pct),
    },
    {
      // How far into a title the people who gave up had got. High completion and a high
      // abandon point are different stories: the second says the ending is the problem.
      ...numericColumn('avg_abandon_pct', 'Mean abandon point', (row) => row.avg_abandon_pct, 'percent'),
      render: (row) => formatPercent(row.avg_abandon_pct),
    },
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    numericColumn('avg_rating', 'Rating', (row) => row.avg_rating),
    numericColumn('popularity_score', 'Popularity', (row) => row.popularity_score),
  ]

  const genreColumns: Column<(typeof genrePerf.rows)[number]>[] = [
    { key: 'genre', header: 'Genre', value: (row) => row.genre, className: 'font-medium' },
    numericColumn('titles', 'Titles', (row) => row.titles),
    numericColumn('originals', 'Originals', (row) => row.originals),
    numericColumn('series_count', 'Series', (row) => row.series_count),
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    {
      ...numericColumn('catalogue_share_pct', 'Catalogue share', (row) => row.catalogue_share_pct, 'percent'),
      render: (row) => formatPercent(row.catalogue_share_pct),
    },
    {
      ...numericColumn('watch_share_pct', 'Watch share', (row) => row.watch_share_pct, 'percent'),
      render: (row) => formatPercent(row.watch_share_pct),
    },
    {
      // Watch share over catalogue share. Above 1 means the genre earns more attention than
      // its shelf space; the API computes it, so it is not recomputed here.
      ...numericColumn('watch_per_title_index', 'Watch index', (row) => row.watch_per_title_index),
      render: (row) =>
        isAbsent(row.watch_per_title_index) ? EMPTY : `${formatNumber(row.watch_per_title_index)}×`,
    },
    numericColumn('watch_hours_per_title', 'Hours per title', (row) => row.watch_hours_per_title, 'hours'),
    numericColumn('unique_viewers', 'Viewers', (row) => row.unique_viewers, 'users'),
    {
      ...numericColumn('completion_rate_pct', 'Completion', (row) => row.completion_rate_pct, 'percent'),
      render: (row) => formatPercent(row.completion_rate_pct),
    },
    {
      // Deliberately not called a conversion rate, and deliberately not a percentage: it
      // exceeds 100% wherever people resume a series without revisiting its detail page.
      ...numericColumn('view_to_start_pct', 'Starts per detail view', (row) => row.view_to_start_pct),
      render: (row) =>
        isAbsent(row.view_to_start_pct)
          ? EMPTY
          : // Divided by 100 because the API pre-multiplies percentages, and this is rendered as
            // a ratio rather than a percentage — 238.5 means 2.39 starts per detail view.
            `${formatNumber(row.view_to_start_pct / 100, 2)}×`,
    },
    numericColumn('avg_rating', 'Rating', (row) => row.avg_rating),
    numericColumn('avg_runtime_minutes', 'Mean runtime', (row) => row.avg_runtime_minutes),
  ]

  const affinityColumns: Column<AffinityCell>[] = [
    { key: 'persona', header: 'Persona', value: (row) => row.persona, className: 'font-medium' },
    { key: 'genre', header: 'Genre', value: (row) => row.genre },
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    {
      ...numericColumn(
        'pct_of_persona_watch',
        'Share of persona watch',
        (row) => row.pct_of_persona_watch,
        'percent',
      ),
      render: (row) => formatPercent(row.pct_of_persona_watch),
    },
    {
      ...numericColumn('affinity_lift', 'Lift vs everyone', (row) => row.affinity_lift),
      render: (row) => (isAbsent(row.affinity_lift) ? EMPTY : `${formatNumber(row.affinity_lift)}×`),
    },
    {
      ...numericColumn('rank_within_persona', 'Rank', (row) => row.rank_within_persona),
      render: (row) => (isAbsent(row.rank_within_persona) ? EMPTY : formatNumber(row.rank_within_persona)),
    },
  ]

  const shelfLifeColumns: Column<(typeof shelfLife.rows)[number]>[] = [
    { key: 'week_since_added', header: 'Week', value: (row) => row.week_since_added, align: 'right' },
    numericColumn('titles', 'Titles', (row) => row.titles),
    numericColumn('starts', 'Starts', (row) => row.starts),
    numericColumn('watch_hours', 'Watch hours', (row) => row.watch_hours, 'hours'),
    {
      ...numericColumn('pct_of_week0_mean', 'Mean vs week 0', (row) => row.pct_of_week0_mean, 'percent'),
      render: (row) => formatPercent(row.pct_of_week0_mean),
    },
    {
      ...numericColumn('pct_of_week0_median', 'Median vs week 0', (row) => row.pct_of_week0_median, 'percent'),
      render: (row) => formatPercent(row.pct_of_week0_median),
    },
    {
      ...numericColumn('pct_of_week0_originals', 'Originals', (row) => row.pct_of_week0_originals, 'percent'),
      render: (row) => formatPercent(row.pct_of_week0_originals),
    },
    {
      ...numericColumn('pct_of_week0_licensed', 'Licensed', (row) => row.pct_of_week0_licensed, 'percent'),
      render: (row) => formatPercent(row.pct_of_week0_licensed),
    },
  ]

  const trailerColumns: Column<(typeof trailer.rows)[number]>[] = [
    {
      key: 'title',
      header: 'Title',
      value: (row) => row.title,
      className: 'font-medium',
      render: (row) => (
        <span className="flex items-center gap-1.5">
          {row.title}
          {row.is_original && (
            <Badge variant="secondary" className="shrink-0">
              original
            </Badge>
          )}
        </span>
      ),
    },
    { key: 'genre', header: 'Genre', value: (row) => row.genre },
    { key: 'content_type', header: 'Type', value: (row) => row.content_type },
    numericColumn('detail_views', 'Detail views', (row) => row.detail_views),
    numericColumn('trailer_views', 'Trailer views', (row) => row.trailer_views),
    numericColumn('starts', 'Starts', (row) => row.starts),
    {
      ...numericColumn('trailer_view_rate_pct', 'Watched trailer', (row) => row.trailer_view_rate_pct, 'percent'),
      render: (row) => formatPercent(row.trailer_view_rate_pct),
    },
    {
      ...numericColumn('trailer_to_start_pct', 'Started after trailer', (row) => row.trailer_to_start_pct, 'percent'),
      render: (row) => formatPercent(row.trailer_to_start_pct),
    },
    {
      ...numericColumn(
        'start_without_trailer_pct',
        'Started without',
        (row) => row.start_without_trailer_pct,
        'percent',
      ),
      render: (row) => formatPercent(row.start_without_trailer_pct),
    },
    {
      ...numericColumn('lift_vs_no_trailer', 'Lift', (row) => row.lift_vs_no_trailer),
      render: (row) =>
        isAbsent(row.lift_vs_no_trailer) ? EMPTY : `${formatNumber(row.lift_vs_no_trailer)}×`,
    },
    {
      ...numericColumn('completion_rate_pct', 'Completion', (row) => row.completion_rate_pct, 'percent'),
      render: (row) => formatPercent(row.completion_rate_pct),
    },
  ]

  return (
    <div className="space-y-4">
      <ChartCard
        title="Most-watched titles"
        definition="Titles ranked by watch hours in the window. Hours per viewer separates a title many people sampled from one a few people lived inside."
        {...topWatch.boundary}
        onExport={() =>
          exportRows('content-top-watch-time', topWatch.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          <CategoryBarChart
            data={topWatchChartRows}
            categoryKey="title"
            unit="hours"
            hideLegend
            categoryWidth={170}
            height={Math.max(220, topWatchChartRows.length * 24)}
            series={[{ key: 'watch_hours', label: 'Watch hours' }]}
          />

          <p className="text-2xs text-muted-foreground">
            The chart shows the top {CHART_TITLE_COUNT} of {formatNumber(topWatch.rows.length)}{' '}
            returned titles; the table below carries all of them, sortable by any column.
          </p>

          <DataTable
            rows={topWatch.rows}
            columns={topWatchColumns}
            rowKey={(row) => String(row.content_id)}
            maxHeight="26rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Completion and where people give up"
        definition="Ranked by completion rate, over titles with at least 25 starts — the API's own floor, because a 100% rate over three starts is noise wearing a ranking's clothes."
        {...completion.boundary}
        onExport={() =>
          exportRows('content-completion-rate', completion.rows, { window: window ?? undefined })
        }
      >
        <DataTable
          rows={completion.rows}
          columns={completionColumns}
          rowKey={(row) => String(row.content_id)}
          maxHeight="26rem"
        />
      </ChartCard>

      <ChartCard
        title="Genre performance"
        definition="Every genre's shelf space against the attention it earns. Catalogue share is the proportion of titles; watch share is the proportion of hours. The gap between them is the point."
        {...genrePerf.boundary}
        onExport={() =>
          exportRows('content-genre-performance', genrePerf.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-4">
          {/* Grouped, not stacked. These are two shares of two different wholes, and stacking
              them would draw a total that means nothing. */}
          <CategoryBarChart
            data={genrePerf.rows}
            categoryKey="genre"
            unit="percent"
            categoryWidth={110}
            height={Math.max(260, genrePerf.rows.length * 26)}
            series={[
              { key: 'catalogue_share_pct', label: 'Share of catalogue' },
              { key: 'watch_share_pct', label: 'Share of watch hours' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            Starts per detail view exceeds 1× for most genres, and that is not an error: a viewer
            resumes a series from the home rail without passing its detail page again, so starts
            outnumber detail views wherever repeat viewing is normal. It is a ratio, not a
            conversion rate that has somehow broken past 100%.
          </p>

          <DataTable
            rows={genrePerf.rows}
            columns={genreColumns}
            rowKey={(row) => row.genre}
            maxHeight="26rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="What each persona watches"
        definition="Share of a persona's watch time by genre. Personas are assigned at signup and drive watch behaviour, so this matrix is the clearest view of the dataset's causal design."
        {...affinity.boundary}
        isEmpty={affinityGrid.length === 0 && affinity.boundary.isEmpty}
        onExport={() =>
          exportRows('content-genre-affinity', affinityGrid, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <HeatmapMatrix
            rows={affinityGrid}
            rowKey={(row) => row.genre}
            columnKey={(row) => row.persona}
            value={(row) => row.pct_of_persona_watch}
            unit="percent"
            rowLabel="Genre"
            detail={(row) =>
              isAbsent(row.affinity_lift)
                ? 'no watch time'
                : `${formatNumber(row.affinity_lift)}× the population rate · rank ${formatNumber(row.rank_within_persona)} for this persona`
            }
          />

          <p className="text-2xs text-muted-foreground">
            Colour is each genre&apos;s share of that persona&apos;s watch time, so a column adds
            to 100%. The affinity lift — how much more a persona watches a genre than everyone
            else does — is in the tooltip and the table, not the colour: it is a ratio centred on
            1×, and a ramp running from zero would paint &ldquo;exactly average&rdquo; as though
            it were &ldquo;never touches it&rdquo;.
          </p>

          <DataTable
            rows={affinityGrid}
            columns={affinityColumns}
            rowKey={(row) => `${row.persona}-${row.genre}`}
            maxHeight="24rem"
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Shelf life"
        definition="Watch hours by week since a title was added, indexed to its first week. Originals and licensed titles are tracked separately because they are acquired for different reasons."
        {...shelfLife.boundary}
        onExport={() =>
          exportRows('content-shelf-life-decay', shelfLife.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <TimeSeriesChart
            data={shelfLife.rows}
            xKey="week_since_added"
            xFormat="raw"
            unit="percent"
            series={[
              { key: 'pct_of_week0_mean', label: 'Mean' },
              { key: 'pct_of_week0_median', label: 'Median' },
              { key: 'pct_of_week0_originals', label: 'Originals' },
              { key: 'pct_of_week0_licensed', label: 'Licensed' },
            ]}
          />

          <p className="text-2xs text-muted-foreground">
            Read this one with the title count beside it. Only one or two titles fall in each week
            bucket on the current window, so a single release moves the whole line — which is why
            the curve rises above 100% before it falls, rather than decaying cleanly. A gap in the
            licensed series is a week with no licensed titles at all: undefined, not zero.
          </p>

          <DataTable
            rows={shelfLife.rows}
            columns={shelfLifeColumns}
            rowKey={(row) => String(row.week_since_added)}
          />
        </div>
      </ChartCard>

      <ChartCard
        title="Do trailers earn starts?"
        definition="For each title: how many detail-page visitors watched the trailer, how many of those went on to start it, and how that compares with the visitors who skipped it. Titles with at least 25 starts."
        {...trailer.boundary}
        onExport={() =>
          exportRows('content-trailer-to-start', trailer.rows, { window: window ?? undefined })
        }
      >
        <div className="space-y-3">
          <p className="text-2xs text-muted-foreground">
            Lift is the trailer group&apos;s start rate over the no-trailer group&apos;s. A lift
            near 1× means the trailer changed nothing measurable — which is the honest reading for
            most of this catalogue, and worth more than a number that flatters the trailer.
          </p>

          <DataTable
            rows={trailer.rows}
            columns={trailerColumns}
            rowKey={(row) => row.title}
            maxHeight="26rem"
          />
        </div>
      </ChartCard>
    </div>
  )
}
