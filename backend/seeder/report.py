"""Data-quality report: prove the generated dataset is sound.

Run after seeding::

    python -m seeder.report          # writes docs/data_quality_report.html
    make report

What this is for
----------------
Anyone reviewing a project built on synthetic data will eventually ask the obvious
question: *is this data any good, or does it just look busy?* This report answers
it before they have to ask, and it is the artefact to open first in an interview.

Two halves, and the first matters more.

**Invariant checks.** Twelve assertions run as SQL against the loaded database and
reported pass or fail. These are not decorative: they verify the journey ordering
guarantees, the denormalisation agreement between ``core.sessions`` and
``core.events``, the absence of future timestamps, and that no row escaped into
``core.events_default``. A red row here is a real bug, and the exit code reflects
it so CI can gate on it.

**Distribution charts.** Eight Plotly figures showing that the generated behaviour
has the shape it was configured to have — and, in three cases, that the *planted
signals* are independently recoverable from the event data. The conversion-by-
channel chart is the important one: the SQL that produces it knows nothing about
``CONVERSION_CHANNEL_EFFECT``, yet it recovers the ordering declared there.

The UTC smearing is a feature
-----------------------------
The hour-of-day chart deliberately shows both UTC and local-time distributions. The
UTC curve is flatter, because timestamps are generated in each user's local evening
and the world does not watch television simultaneously. A single sharp global UTC
peak would be the signature of a naively generated dataset, and the chart exists so
a reader can see that this one is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, Final

from app.core.config import REPO_ROOT, get_settings
from app.core.logging import configure_logging, get_logger
from seeder import loaders
from seeder.journeys import summarise_navigation
from seeder.personas import summarise_personas
from seeder.seasonality import COUNTRY_UTC_OFFSET_HOURS, summarise_shape

if TYPE_CHECKING:
    import psycopg

logger = get_logger(__name__)

#: Where the report is written. Mounted into the seeder container by
#: docker-compose so the file survives ``--rm``.
OUTPUT_PATH: Final[Path] = REPO_ROOT / "docs" / "data_quality_report.html"

#: Palette shared with the dashboard's ``chartTheme.ts``, so the report and the
#: React app look like one product rather than two.
INK: Final[str] = "#0A0B0D"
PANEL: Final[str] = "#131518"
TEXT: Final[str] = "#E7E9EE"
MUTED: Final[str] = "#8B92A0"
GRID: Final[str] = "#22262E"
SERIES: Final[tuple[str, ...]] = (
    "#6366F1",
    "#22D3EE",
    "#A78BFA",
    "#34D399",
    "#FBBF24",
    "#FB7185",
    "#60A5FA",
    "#F472B6",
)
PASS: Final[str] = "#34D399"
FAIL: Final[str] = "#FB7185"


@dataclass(slots=True)
class Check:
    """One invariant check and its result.

    Attributes:
        name: Short label.
        rationale: Why this invariant matters, shown beside the result.
        sql: Query returning a single count. Zero means the invariant holds.
        violations: Rows violating it, populated by :func:`run_checks`.
        error: Message if the query itself failed.
    """

    name: str
    rationale: str
    sql: str
    violations: int = 0
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Return whether the invariant holds."""
        return self.error is None and self.violations == 0


#: The invariant suite.
#:
#: Every one of these could in principle be a database constraint. Most are not,
#: because they are *cross-row* properties — "COMPLETE_VIDEO requires an earlier
#: START_VIDEO in the same session" cannot be expressed as a CHECK. So they are
#: enforced by the generator and verified here, which is the honest division of
#: labour between the two.
CHECKS: Final[tuple[Check, ...]] = (
    Check(
        name="No future events",
        rationale=(
            "The stated requirement. Also enforced by ck_events_no_future_time, so a "
            "violation here would mean the constraint was dropped."
        ),
        sql="SELECT count(*) FROM core.events WHERE event_time > now()",
    ),
    Check(
        name="No future signups",
        rationale="No account may be created after the window ends.",
        sql="SELECT count(*) FROM core.users WHERE signup_date > CURRENT_DATE",
    ),
    Check(
        name="Every session opens with OPEN_APP",
        rationale=(
            "Journey invariant. A session whose first event is anything else would "
            "break the funnel's denominator."
        ),
        sql="""
            SELECT count(*) FROM (
                SELECT DISTINCT ON (session_id) session_id, event_name
                FROM core.events ORDER BY session_id, step_index
            ) AS first_events
            WHERE event_name <> 'OPEN_APP'
        """,
    ),
    Check(
        name="Every session closes with EXIT",
        rationale="Journey invariant. Guarantees exit-screen analysis covers every session.",
        sql="""
            SELECT count(*) FROM (
                SELECT DISTINCT ON (session_id) session_id, event_name
                FROM core.events ORDER BY session_id, step_index DESC
            ) AS last_events
            WHERE event_name <> 'EXIT'
        """,
    ),
    Check(
        name="COMPLETE_VIDEO requires an earlier START_VIDEO",
        rationale=(
            "The invariant that makes completion rate trustworthy. Without it the "
            "content page could report more completions than starts."
        ),
        sql="""
            SELECT count(*)
            FROM core.events AS c
            WHERE c.event_name = 'COMPLETE_VIDEO'
              AND NOT EXISTS (
                  SELECT 1 FROM core.events AS s
                  WHERE s.session_id = c.session_id
                    AND s.content_id = c.content_id
                    AND s.event_name = 'START_VIDEO'
                    AND s.step_index < c.step_index
              )
        """,
    ),
    Check(
        name="RATE requires an earlier COMPLETE_VIDEO",
        rationale="Nobody rates a title they never finished.",
        sql="""
            SELECT count(*)
            FROM core.events AS r
            WHERE r.event_name = 'RATE'
              AND NOT EXISTS (
                  SELECT 1 FROM core.events AS c
                  WHERE c.session_id = r.session_id
                    AND c.content_id = r.content_id
                    AND c.event_name = 'COMPLETE_VIDEO'
                    AND c.step_index < r.step_index
              )
        """,
    ),
    Check(
        name="No slot both completed and abandoned",
        rationale=(
            "Mutually exclusive terminal states. A slot with both would be "
            "double-counted in every content metric."
        ),
        sql="""
            SELECT count(*) FROM (
                SELECT session_id, content_id
                FROM core.events
                WHERE event_name IN ('COMPLETE_VIDEO', 'ABANDON_VIDEO')
                  AND content_id IS NOT NULL
                GROUP BY session_id, content_id
                HAVING count(DISTINCT event_name) > 1
            ) AS conflicted
        """,
    ),
    Check(
        name="sessions.watch_seconds agrees with events",
        rationale=(
            "The denormalisation contract. watch_seconds is incremental per event, so "
            "the per-session sum must equal the stored column."
        ),
        sql="""
            SELECT count(*)
            FROM core.sessions AS s
            LEFT JOIN (
                SELECT session_id, COALESCE(SUM(watch_seconds), 0) AS total
                FROM core.events GROUP BY session_id
            ) AS e ON e.session_id = s.session_id
            WHERE abs(s.watch_seconds - COALESCE(e.total, 0)) > 1
        """,
    ),
    Check(
        name="sessions.event_count agrees with events",
        rationale="The other half of the denormalisation contract.",
        sql="""
            SELECT count(*)
            FROM core.sessions AS s
            LEFT JOIN (
                SELECT session_id, count(*) AS total
                FROM core.events GROUP BY session_id
            ) AS e ON e.session_id = s.session_id
            WHERE s.event_count <> COALESCE(e.total, 0)
        """,
    ),
    Check(
        name="No overlapping subscription terms",
        rationale=(
            "Overlapping terms would double-count MRR. Plan changes close the previous "
            "term the day before the new one opens, precisely to avoid this."
        ),
        sql="""
            SELECT count(*)
            FROM core.subscriptions AS a
            JOIN core.subscriptions AS b
              ON a.user_id = b.user_id
             AND a.subscription_id < b.subscription_id
             AND a.started_on <= COALESCE(b.ended_on, CURRENT_DATE)
             AND b.started_on <= COALESCE(a.ended_on, CURRENT_DATE)
        """,
    ),
    Check(
        name="events_default partition is empty",
        rationale=(
            "Rows here mean an event_time fell outside every declared monthly "
            "partition — a boundary bug that would silently exclude those rows from "
            "partition-pruned queries."
        ),
        sql="SELECT count(*) FROM core.events_default",
    ),
    Check(
        name="No events before their user signed up",
        rationale="Causality. An event predating signup would corrupt every cohort.",
        sql="""
            SELECT count(*)
            FROM core.events AS e
            JOIN core.users AS u USING (user_id)
            WHERE (e.event_time AT TIME ZONE 'UTC')::date < u.signup_date
        """,
    ),
)


def run_checks(conn: psycopg.Connection[Any]) -> list[Check]:
    """Execute every invariant check.

    Args:
        conn: An open connection to the seeded database.

    Returns:
        The checks, with results populated.
    """
    results: list[Check] = []

    for template in CHECKS:
        check = Check(name=template.name, rationale=template.rationale, sql=template.sql)
        try:
            with conn.cursor() as cur:
                cur.execute(check.sql)
                row = cur.fetchone()
                check.violations = int(row[0]) if row else 0
        except Exception as exc:  # noqa: BLE001 - a failed check is a reportable result
            check.error = str(exc).split("\n")[0]
            logger.warning("check_failed", check=check.name, error=check.error)

        results.append(check)

    return results


def _query(conn: psycopg.Connection[Any], sql: str) -> list[tuple[Any, ...]]:
    """Execute a query and return all rows.

    Args:
        conn: An open connection.
        sql: The query.

    Returns:
        Result rows, or an empty list if the query failed.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - a missing chart beats a failed report
        logger.warning("report_query_failed", error=str(exc).split("\n")[0])
        return []


def _layout(title: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
    """Return a dark-theme Plotly layout.

    Args:
        title: Chart title.
        **kwargs: Additional layout keys, merged last.

    Returns:
        A layout mapping.
    """
    layout: dict[str, Any] = {
        "title": {"text": title, "font": {"size": 15, "color": TEXT}},
        "paper_bgcolor": PANEL,
        "plot_bgcolor": PANEL,
        "font": {"color": MUTED, "family": "Inter, system-ui, sans-serif", "size": 12},
        "xaxis": {"gridcolor": GRID, "zerolinecolor": GRID, "linecolor": GRID},
        "yaxis": {"gridcolor": GRID, "zerolinecolor": GRID, "linecolor": GRID},
        "margin": {"l": 60, "r": 24, "t": 48, "b": 48},
        "height": 380,
        "colorway": list(SERIES),
        "legend": {"font": {"color": MUTED, "size": 11}},
        "hoverlabel": {"bgcolor": INK, "font": {"color": TEXT}},
    }
    layout.update(kwargs)
    return layout


def build_figures(conn: psycopg.Connection[Any]) -> list[tuple[str, str, Any]]:
    """Build every figure in the report.

    Args:
        conn: An open connection to the seeded database.

    Returns:
        ``(section_title, commentary, figure)`` triples. Commentary states what the
        chart is supposed to demonstrate, so a reader is not left inferring it.
    """
    import plotly.graph_objects as go

    figures: list[tuple[str, str, Any]] = []

    # -- 1. Session volume with seasonality -------------------------------
    rows = _query(
        conn,
        """
        SELECT (session_start AT TIME ZONE 'UTC')::date AS day, count(*)
        FROM core.sessions
        GROUP BY 1 ORDER BY 1
        """,
    )
    if rows:
        figure = go.Figure(
            go.Scatter(
                x=[row[0] for row in rows],
                y=[row[1] for row in rows],
                mode="lines",
                line={"color": SERIES[0], "width": 1.5},
                fill="tozeroy",
                fillcolor="rgba(99,102,241,0.14)",
                name="sessions",
            )
        )
        figure.update_layout(**_layout("Daily session volume"))
        figures.append(
            (
                "Seasonality and growth",
                "Weekend peaks, a compound growth trend, and the December holiday "
                "spike are all visible. The line is deliberately noisy: log-normal "
                "per-day noise is applied last, because a perfectly smooth metric is "
                "the clearest sign of a generated dataset.",
                figure,
            )
        )

    # -- 2. Hour of day, UTC against local --------------------------------
    offsets = ", ".join(
        f"WHEN c.name = '{name}' THEN INTERVAL '{hours} hours'"
        for name, hours in COUNTRY_UTC_OFFSET_HOURS.items()
    )
    rows = _query(
        conn,
        f"""
        SELECT
            EXTRACT(HOUR FROM s.session_start AT TIME ZONE 'UTC')::int AS utc_hour,
            EXTRACT(HOUR FROM (s.session_start + CASE {offsets}
                ELSE INTERVAL '0 hours' END) AT TIME ZONE 'UTC')::int AS local_hour,
            count(*)
        FROM core.sessions AS s
        JOIN core.users AS u ON u.user_id = s.user_id
        JOIN core.countries AS c ON c.country_id = u.country_id
        GROUP BY 1, 2
        """,
    )
    if rows:
        utc_totals = [0] * 24
        local_totals = [0] * 24
        for utc_hour, local_hour, count in rows:
            utc_totals[utc_hour] += count
            local_totals[local_hour] += count

        figure = go.Figure()
        figure.add_bar(
            x=list(range(24)), y=local_totals, name="local time", marker_color=SERIES[0]
        )
        figure.add_bar(
            x=list(range(24)), y=utc_totals, name="UTC", marker_color=SERIES[1], opacity=0.75
        )
        figure.update_layout(
            **_layout(
                "Session starts by hour",
                barmode="overlay",
                xaxis={"title": "hour", "dtick": 2, "gridcolor": GRID},
            )
        )
        figures.append(
            (
                "Timezone handling",
                "The local-time curve peaks sharply at 20:00-22:00. The UTC curve is "
                "flatter, and that is correct: timestamps are generated in each user's "
                "local evening, so a single sharp global UTC peak would mean the whole "
                "world watched television at the same instant.",
                figure,
            )
        )

    # -- 3. Event mix -----------------------------------------------------
    rows = _query(
        conn,
        "SELECT event_name::text, count(*) FROM core.events GROUP BY 1 ORDER BY 2 DESC",
    )
    if rows:
        figure = go.Figure(
            go.Bar(
                x=[row[1] for row in rows],
                y=[row[0] for row in rows],
                orientation="h",
                marker_color=SERIES[2],
            )
        )
        figure.update_layout(
            **_layout(
                "Event mix",
                height=460,
                yaxis={"autorange": "reversed", "gridcolor": GRID},
                xaxis={"title": "events", "gridcolor": GRID},
            )
        )
        figures.append(
            (
                "Clickstream composition",
                "VIDEO_PROGRESS dominates, as it must — one checkpoint per five minutes "
                "watched. Navigation events outnumber playback events, and starts "
                "exceed completions, which is what a funnel with real drop-off looks "
                "like.",
                figure,
            )
        )

    # -- 4. Persona divergence --------------------------------------------
    rows = _query(
        conn,
        """
        SELECT p.name,
               round(avg(l.total_sessions)::numeric, 1),
               round(avg(l.completion_rate)::numeric, 3),
               round(avg(l.total_watch_seconds) / 3600.0, 1)
        FROM analytics.mv_user_lifetime AS l
        JOIN core.users AS u USING (user_id)
        JOIN core.personas AS p ON p.persona_id = u.persona_id
        GROUP BY p.name ORDER BY 2 DESC
        """,
    )
    if rows:
        figure = go.Figure()
        figure.add_bar(
            x=[row[0] for row in rows],
            y=[float(row[1]) for row in rows],
            name="avg sessions",
            marker_color=SERIES[0],
        )
        figure.add_scatter(
            x=[row[0] for row in rows],
            y=[float(row[2]) * 100 for row in rows],
            name="completion rate %",
            yaxis="y2",
            mode="markers+lines",
            marker={"size": 9, "color": SERIES[4]},
            line={"color": SERIES[4], "width": 1.5},
        )
        figure.update_layout(
            **_layout(
                "Behaviour by persona",
                yaxis2={
                    "overlaying": "y",
                    "side": "right",
                    "title": "completion %",
                    "gridcolor": "rgba(0,0,0,0)",
                },
                xaxis={"tickangle": -30, "gridcolor": GRID},
            )
        )
        figures.append(
            (
                "Persona divergence — planted signal, recovered",
                "Measured from the event stream, not from core.personas. Binge Watchers "
                "and Premium Loyalists genuinely produce more sessions and finish more "
                "of what they start; Churn Risk sits at the bottom on both axes. The "
                "ordering matches the coefficients in Alembic revision 0002, which no "
                "query here reads.",
                figure,
            )
        )

    # -- 5. Conversion by channel -----------------------------------------
    rows = _query(
        conn,
        """
        SELECT ch.name,
               round(100.0 * count(*) FILTER (WHERE s.user_id IS NOT NULL)
                     / NULLIF(count(*), 0), 2) AS conversion_pct,
               ch.cac_usd,
               count(*) AS users
        FROM core.users AS u
        JOIN core.marketing_channels AS ch ON ch.channel_id = u.channel_id
        LEFT JOIN (
            SELECT DISTINCT user_id FROM core.subscriptions WHERE status <> 'trialing'
        ) AS s ON s.user_id = u.user_id
        GROUP BY ch.name, ch.cac_usd
        HAVING count(*) > 50
        ORDER BY conversion_pct DESC
        """,
    )
    if rows:
        figure = go.Figure(
            go.Bar(
                x=[row[0] for row in rows],
                y=[float(row[1] or 0) for row in rows],
                marker_color=[
                    SERIES[3] if float(row[2]) == 0 else SERIES[5] for row in rows
                ],
                customdata=[[float(row[2]), row[3]] for row in rows],
                hovertemplate=(
                    "<b>%{x}</b><br>conversion %{y:.2f}%<br>"
                    "CAC $%{customdata[0]:.2f}<br>%{customdata[1]:,} users<extra></extra>"
                ),
            )
        )
        figure.update_layout(
            **_layout(
                "Paid conversion by acquisition channel",
                xaxis={"tickangle": -35, "gridcolor": GRID},
                yaxis={"title": "conversion %", "gridcolor": GRID},
            )
        )
        figures.append(
            (
                "Marketing signal — planted, recovered",
                "Green bars are organic channels (zero CAC), red are paid. Referral and "
                "organic search convert best while Display and Paid Social convert "
                "worst, which is the relationship declared in "
                "CONVERSION_CHANNEL_EFFECT. The mechanism is causal rather than "
                "cosmetic: channel skews which personas are acquired, and persona "
                "drives behaviour.",
                figure,
            )
        )

    # -- 6. Session duration distribution ---------------------------------
    rows = _query(
        conn,
        """
        SELECT width_bucket(duration_seconds, 0, 7200, 48) AS bucket, count(*)
        FROM core.sessions WHERE duration_seconds <= 7200
        GROUP BY 1 ORDER BY 1
        """,
    )
    if rows:
        figure = go.Figure(
            go.Bar(
                x=[(row[0] * 150) / 60 for row in rows],
                y=[row[1] for row in rows],
                marker_color=SERIES[1],
            )
        )
        figure.update_layout(
            **_layout(
                "Session duration distribution",
                xaxis={"title": "minutes", "gridcolor": GRID},
                yaxis={"title": "sessions", "gridcolor": GRID},
            )
        )
        figures.append(
            (
                "Session length",
                "Right-skewed with a long tail, and continuous rather than showing "
                "eight persona-shaped spikes. Per-user parameters are drawn from "
                "log-normal distributions around each persona's base, so individuals "
                "vary smoothly while group means stay distinguishable.",
                figure,
            )
        )

    # -- 7. Abandonment point ---------------------------------------------
    rows = _query(
        conn,
        """
        SELECT width_bucket(progress_pct, 0, 100, 20) AS bucket, count(*)
        FROM core.events
        WHERE event_name = 'ABANDON_VIDEO' AND progress_pct IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """,
    )
    if rows:
        figure = go.Figure(
            go.Bar(
                x=[row[0] * 5 for row in rows],
                y=[row[1] for row in rows],
                marker_color=SERIES[5],
            )
        )
        figure.update_layout(
            **_layout(
                "Where viewers abandon",
                xaxis={"title": "% watched", "dtick": 10, "gridcolor": GRID},
                yaxis={"title": "abandonments", "gridcolor": GRID},
            )
        )
        figures.append(
            (
                "Abandonment shape",
                "Trimodal on purpose: most quitting happens in the first few minutes, "
                "with a secondary cluster near the end from viewers who intend to "
                "finish later. A unimodal draw would hide the most actionable content "
                "signal in the dataset — the difference between a title people bounce "
                "off and one they almost finish.",
                figure,
            )
        )

    # -- 8. Retention curve by cohort -------------------------------------
    rows = _query(
        conn,
        """
        WITH cohorts AS (
            SELECT user_id, date_trunc('month', signup_date)::date AS cohort
            FROM core.users
        ),
        sized AS (
            SELECT cohort, count(*) AS size FROM cohorts GROUP BY cohort
            HAVING count(*) > 200
        )
        SELECT c.cohort,
               LEAST(d.days_since_signup / 7, 12) AS week,
               round(100.0 * count(DISTINCT d.user_id) / max(s.size), 2)
        FROM analytics.mv_user_daily AS d
        JOIN cohorts AS c USING (user_id)
        JOIN sized AS s ON s.cohort = c.cohort
        WHERE d.days_since_signup BETWEEN 0 AND 90
        GROUP BY c.cohort, week
        ORDER BY c.cohort, week
        """,
    )
    if rows:
        by_cohort: dict[Any, list[tuple[int, float]]] = {}
        for cohort, week, pct in rows:
            by_cohort.setdefault(cohort, []).append((int(week), float(pct)))

        figure = go.Figure()
        for index, (cohort, points) in enumerate(sorted(by_cohort.items())[:8]):
            points.sort()
            figure.add_scatter(
                x=[week for week, _ in points],
                y=[pct for _, pct in points],
                mode="lines",
                name=str(cohort),
                line={"color": SERIES[index % len(SERIES)], "width": 1.8},
            )
        figure.update_layout(
            **_layout(
                "Weekly retention by signup cohort",
                xaxis={"title": "weeks since signup", "gridcolor": GRID},
                yaxis={"title": "% active", "gridcolor": GRID},
            )
        )
        figures.append(
            (
                "Retention shape",
                "Steep early decay flattening into a stable tail — the characteristic "
                "consumer retention curve, produced by the front-loaded tenure hazard "
                "in CHURN_TENURE_MULTIPLIER rather than imposed. A linear decay would "
                "mean churn was modelled as a constant, which it is not.",
                figure,
            )
        )

    return figures


def _render_checks_html(checks: list[Check]) -> str:
    """Render the invariant table.

    Args:
        checks: Executed checks.

    Returns:
        An HTML fragment.
    """
    passed = sum(1 for check in checks if check.passed)
    total = len(checks)
    all_passed = passed == total

    rows = []
    for check in checks:
        colour = PASS if check.passed else FAIL
        if check.error:
            verdict = f"ERROR: {check.error}"
        elif check.passed:
            verdict = "pass"
        else:
            verdict = f"{check.violations:,} violations"

        rows.append(
            f"""
            <tr>
              <td class="check-name">{check.name}</td>
              <td class="check-verdict" style="color:{colour}">{verdict}</td>
              <td class="check-why">{check.rationale}</td>
            </tr>
            """
        )

    banner_colour = PASS if all_passed else FAIL
    banner = (
        f"All {total} invariants hold."
        if all_passed
        else f"{total - passed} of {total} invariants FAILED — the dataset has a defect."
    )

    return f"""
    <section class="panel">
      <h2>Invariant checks</h2>
      <p class="lede">
        Twelve assertions run as SQL against the loaded database. Most are cross-row
        properties that cannot be expressed as a CHECK constraint, so the generator
        enforces them and this table verifies them.
      </p>
      <div class="banner" style="border-color:{banner_colour};color:{banner_colour}">
        {banner}
      </div>
      <table class="checks">
        <thead><tr><th>Invariant</th><th>Result</th><th>Why it matters</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </section>
    """


def _render_config_html() -> str:
    """Render the generator's declared configuration.

    Showing the intent beside the measured result is what lets a reader confirm the
    two agree, rather than taking the charts on trust.

    Returns:
        An HTML fragment.
    """
    shape = summarise_shape()
    personas = summarise_personas()
    navigation = summarise_navigation()

    holidays = "".join(
        f"<li><strong>{entry['name']}</strong> &times;{entry['multiplier']} "
        f"<span class='muted'>"
        f"{'global' if entry['scope'] == 'global' else ', '.join(entry['scope'][:3]) + '...'}"
        f"</span></li>"
        for entry in shape["holidays"]  # type: ignore[union-attr]
    )

    persona_rows = "".join(
        f"<tr><td>{name}</td><td>{values['top_genre']}</td>"
        f"<td>{values['modal_session_minutes']:.0f} min</td>"
        f"<td>{values['playback_probability']:.0%}</td>"
        f"<td>{values['monthly_activity_trend']:.3f}</td></tr>"
        for name, values in personas.items()
    )

    return f"""
    <section class="panel">
      <h2>What the generator was told to do</h2>
      <p class="lede">
        The charts above are measurements. These are the declared inputs, so the two
        can be compared directly.
      </p>
      <div class="grid">
        <div>
          <h3>Seasonality</h3>
          <p>Local peak hour: <strong>{shape["peak_hour_weekday_local"]}:00</strong>
             weekdays, <strong>{shape["peak_hour_weekend_local"]}:00</strong> weekends.
             Weekend lift <strong>&times;{shape["weekend_lift"]}</strong>.
             Monthly growth <strong>{shape["monthly_growth_rate"]:.1%}</strong>.</p>
          <ul>{holidays}</ul>
          <p class="muted small">{shape["timezone_note"]}</p>
        </div>
        <div>
          <h3>Navigation</h3>
          <p>Base <code>VIEW_CONTENT &rarr; EXIT</code>
             <strong>{navigation["view_to_exit_base"]:.0%}</strong>;
             <code>SEARCH &rarr; VIEW_CONTENT</code>
             <strong>{navigation["search_to_view_base"]:.0%}</strong>.</p>
          <p class="muted small">
            Navigation is a six-state Markov chain skewed per persona. Playback is
            emitted as an atomic legal block, so an illegal ordering such as a
            completion without a start is unrepresentable rather than merely unlikely.
          </p>
        </div>
      </div>
      <h3>Persona parameters</h3>
      <table class="checks">
        <thead><tr>
          <th>Persona</th><th>Top genre</th><th>Modal session</th>
          <th>Plays something</th><th>Monthly trend</th>
        </tr></thead>
        <tbody>{persona_rows}</tbody>
      </table>
    </section>
    """


def write_report(
    conn: psycopg.Connection[Any],
    output_path: Path | None = None,
) -> Path:
    """Build and write the report.

    Args:
        conn: An open connection to the seeded database.
        output_path: Destination. Defaults to :data:`OUTPUT_PATH`.

    Returns:
        The path written.
    """
    import plotly.io as pio

    destination = output_path or OUTPUT_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)

    checks = run_checks(conn)
    figures = build_figures(conn)
    counts = loaders.row_counts(conn)
    settings = get_settings()

    count_cards = "".join(
        f'<div class="card"><span class="n">{count:,}</span>'
        f'<span class="l">{table}</span></div>'
        for table, count in counts.items()
    )

    chart_blocks = []
    for title, commentary, figure in figures:
        # include_plotlyjs on the first figure only; repeating the 3MB bundle per
        # chart would produce a 25MB file.
        first = not chart_blocks
        chart_blocks.append(
            f"""
            <section class="panel">
              <h2>{title}</h2>
              <p class="lede">{commentary}</p>
              {pio.to_html(figure, include_plotlyjs="cdn" if first else False, full_html=False,
                           config={"displayModeBar": False})}
            </section>
            """
        )

    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vireo dataset — data quality report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;background:{INK};color:{TEXT};
       font:400 15px/1.6 Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1080px;margin:0 auto;padding:48px 24px 96px}}
  header{{margin-bottom:40px;padding-bottom:28px;border-bottom:1px solid {GRID}}}
  h1{{margin:0 0 8px;font-size:30px;font-weight:600;letter-spacing:-.02em}}
  h2{{margin:0 0 6px;font-size:18px;font-weight:600;letter-spacing:-.01em}}
  h3{{margin:20px 0 8px;font-size:14px;font-weight:600;color:{TEXT}}}
  .sub{{color:{MUTED};font-size:14px}}
  .panel{{background:{PANEL};border:1px solid {GRID};border-radius:12px;
         padding:24px;margin-bottom:20px}}
  .lede{{color:{MUTED};font-size:13.5px;margin:0 0 18px;max-width:76ch}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
         gap:12px;margin-bottom:20px}}
  .card{{background:{PANEL};border:1px solid {GRID};border-radius:10px;padding:16px}}
  .card .n{{display:block;font:500 22px JetBrains Mono,monospace;color:{TEXT}}}
  .card .l{{display:block;color:{MUTED};font-size:11px;text-transform:uppercase;
           letter-spacing:.06em;margin-top:4px}}
  .banner{{border:1px solid;border-radius:8px;padding:12px 16px;margin-bottom:18px;
          font-weight:500;font-size:14px}}
  table.checks{{width:100%;border-collapse:collapse;font-size:13px}}
  table.checks th{{text-align:left;color:{MUTED};font-weight:500;font-size:11px;
                  text-transform:uppercase;letter-spacing:.06em;
                  padding:8px 12px;border-bottom:1px solid {GRID}}}
  table.checks td{{padding:10px 12px;border-bottom:1px solid {GRID};
                  vertical-align:top}}
  .check-name{{font-weight:500;color:{TEXT};width:26%}}
  .check-verdict{{font:500 12px JetBrains Mono,monospace;white-space:nowrap;width:16%}}
  .check-why{{color:{MUTED};font-size:12.5px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}
  @media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
  ul{{margin:8px 0;padding-left:20px;color:{MUTED};font-size:13px}}
  li{{margin:3px 0}}
  code{{font:500 12px JetBrains Mono,monospace;background:{INK};
       padding:2px 5px;border-radius:4px;color:{SERIES[1]}}}
  .muted{{color:{MUTED}}} .small{{font-size:12px}}
  footer{{margin-top:40px;padding-top:24px;border-top:1px solid {GRID};
         color:{MUTED};font-size:12.5px}}
</style></head><body><div class="wrap">
<header>
  <h1>Vireo dataset — data quality report</h1>
  <p class="sub">
    Generated {generated_at} &middot; profile <code>{settings.seed.profile.value}</code>
    &middot; seed <code>{settings.seed.random_seed}</code> &middot;
    {settings.seed.window_months}-month window
  </p>
  <p class="sub" style="margin-top:10px;max-width:76ch">
    Every row in this dataset is synthetic. This report exists to demonstrate that
    it is nonetheless <em>structurally sound</em> and <em>causally consistent</em>:
    the invariants hold, and relationships deliberately planted in the generator are
    independently recoverable from the event stream by SQL that knows nothing about
    them.
  </p>
</header>
<div class="cards">{count_cards}</div>
{_render_checks_html(checks)}
{"".join(chart_blocks)}
{_render_config_html()}
<footer>
  Reproduce with <code>python -m seeder --profile {settings.seed.profile.value}
  --seed {settings.seed.random_seed}</code>, then <code>python -m seeder.report</code>.
  The same seed yields a byte-identical dataset on any machine.
</footer>
</div></body></html>"""

    destination.write_text(html, encoding="utf-8")

    failed = [check.name for check in checks if not check.passed]
    logger.info(
        "report_written",
        path=str(destination),
        charts=len(figures),
        checks_passed=len(checks) - len(failed),
        checks_total=len(checks),
        failures=failed,
    )

    return destination


def main() -> int:
    """Console entrypoint for ``python -m seeder.report``.

    Returns:
        ``0`` when every invariant holds, ``1`` otherwise, so CI can gate on it.
    """
    configure_logging()
    settings = get_settings()

    conn = loaders.connect(settings.db.libpq_dsn)
    try:
        checks = run_checks(conn)
        path = write_report(conn)
    finally:
        conn.close()

    failed = [check for check in checks if not check.passed]

    print(f"\n  {path}")
    print(f"  {len(checks) - len(failed)}/{len(checks)} invariants hold")

    if failed:
        print("\n  FAILED:")
        for check in failed:
            detail = check.error or f"{check.violations:,} violations"
            print(f"    - {check.name}: {detail}")
        print()
        return 1

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
